import tensorflow as tf
import tensorflow.contrib.slim as slim
# import threading
# import multiprocessing
import numpy as np
from utils import *
# import matplotlib.pyplot as plt


class AC_Network():
  def __init__(self,a_size,scope,trainer, width, height):
    with tf.variable_scope(scope):
      #Input and visual encoding layers
      self.state = tf.placeholder(shape=[None, height, width, 3], dtype=tf.float32)

      #Added a conv layer as described here: https://github.com/awjuliani/Meta-RL/blob/master/A3C-Meta-Grid.ipynb
      #parameters: inputs, num_outputs, activation_fn
      self.cnn_first_layer = tf.layers.conv2d(self.state, 16, (8, 8), strides=(4, 4))
      self.cnn_second_layer = tf.layers.conv2d(self.cnn_first_layer, 32, (4, 4), strides=(2, 2))
      self.conv = tf.contrib.layers.fully_connected(slim.flatten(self.cnn_second_layer), 256, activation_fn=tf.nn.relu)

      self.prev_rewards = tf.placeholder(shape=[None, 1], dtype=tf.float32)
      self.prev_actions = tf.placeholder(shape=[None], dtype=tf.int32)
      self.timestep = tf.placeholder(shape=[None, 1], dtype=tf.float32)
      self.prev_actions_onehot = tf.one_hot(self.prev_actions,a_size, dtype=tf.float32)

      #Recurrent network for temporal dependencies
      lstm_cell = tf.nn.rnn_cell.LSTMCell(256, state_is_tuple=True)
      c_init = np.zeros((1, lstm_cell.state_size.c), np.float32)
      h_init = np.zeros((1, lstm_cell.state_size.h), np.float32)
      self.state_init = [c_init, h_init]

      c_in = tf.placeholder(tf.float32, [1, lstm_cell.state_size.c])
      h_in = tf.placeholder(tf.float32, [1, lstm_cell.state_size.h])
      self.state_in = (c_in, h_in)

      hidden = tf.concat([slim.flatten(self.conv),self.prev_rewards,self.prev_actions_onehot,self.timestep],1)
      rnn_in = tf.expand_dims(hidden, [0])

      step_size = tf.shape(self.prev_rewards)[:1]
      state_in = tf.contrib.rnn.LSTMStateTuple(c_in, h_in)

      lstm_outputs, lstm_state = tf.nn.dynamic_rnn(
        lstm_cell,
        rnn_in,
        initial_state=state_in,
        sequence_length=step_size,
        time_major=False)

      lstm_c, lstm_h = lstm_state
      self.state_out = (lstm_c[:1, :], lstm_h[:1, :])
      rnn_out = tf.reshape(lstm_outputs, [-1, 256])

      self.actions = tf.placeholder(shape=[None],dtype=tf.int32)
      self.actions_onehot = tf.one_hot(self.actions,a_size,dtype=tf.float32)

      #Output layers for policy and value estimations
      self.policy = slim.fully_connected(rnn_out,a_size,
        activation_fn=tf.nn.softmax,
        weights_initializer=normalized_columns_initializer(0.01),
        biases_initializer=None)
      self.value = slim.fully_connected(rnn_out,1,
        activation_fn=None,
        weights_initializer=normalized_columns_initializer(1.0),
        biases_initializer=None)

      #Only the worker network need ops for loss functions and gradient updating.
      if scope != 'global':
        self.target_v = tf.placeholder(shape=[None],dtype=tf.float32)
        self.advantages = tf.placeholder(shape=[None],dtype=tf.float32)

        self.responsible_outputs = tf.reduce_sum(self.policy * self.actions_onehot, [1])

        #Loss functions
        self.value_loss = 0.5 * tf.reduce_sum(tf.square(self.target_v - tf.reshape(self.value,[-1])))
        self.entropy = - tf.reduce_sum(self.policy * tf.log(self.policy + 1e-7))
        self.policy_loss = -tf.reduce_sum(tf.log(self.responsible_outputs + 1e-7)*self.advantages)
        self.loss = 0.05 * self.value_loss + self.policy_loss - self.entropy * 0.05

        #Get gradients from local network using local losses
        local_vars = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope)
        self.gradients = tf.gradients(self.loss,local_vars)
        self.var_norms = tf.global_norm(local_vars)
        grads,self.grad_norms = tf.clip_by_global_norm(self.gradients,999.0)

        #Apply local gradients to global network
        global_vars = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, 'global')
        self.apply_grads = trainer.apply_gradients(zip(grads,global_vars))
