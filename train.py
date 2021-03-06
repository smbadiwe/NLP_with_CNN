#! /usr/bin/env python

import tensorflow as tf
import numpy as np
import os
import datetime
import data_helpers
from text_cnn import TextCNN
from tensorflow.contrib import learn
import yaml
import math

# Parameters
# ==================================================

# Data loading params
tf.flags.DEFINE_float("dev_sample_percentage", .1, "Percentage of the training data to use for validation")
tf.flags.DEFINE_string("positive_data_file", "./data/rt-polaritydata/rt-polarity.pos", "Data source for the positive data.")
tf.flags.DEFINE_string("negative_data_file", "./data/rt-polaritydata/rt-polarity.neg", "Data source for the negative data.")

# Model Hyperparameters
tf.flags.DEFINE_boolean("enable_word_embeddings", True, "Enable/disable the word embedding (default: True)")
tf.flags.DEFINE_integer("embedding_dim", 300, "Dimensionality of character embedding (default: 128)")
tf.flags.DEFINE_string("filter_sizes", "3,4,5", "Comma-separated filter sizes (default: '3,4,5')")
tf.flags.DEFINE_integer("num_filters", 128, "Number of filters per filter size (default: 128)")
tf.flags.DEFINE_float("dropout_keep_prob", 1.0, "Dropout keep probability (default: 0.5)")
tf.flags.DEFINE_float("l2_reg_lambda", 0.0, "L2 regularization lambda (default: 0.0)")

# Training parameters
tf.flags.DEFINE_integer("batch_size", 32, "Batch Size (default: 64)")
tf.flags.DEFINE_integer("num_epochs", 300, "Number of training epochs (default: 200)")
tf.flags.DEFINE_integer("evaluate_every", 200, "Evaluate model on dev set after this many steps (default: 100)")
tf.flags.DEFINE_integer("checkpoint_every", 200, "Save model after this many steps (default: 100)")
tf.flags.DEFINE_integer("num_checkpoints", 5, "Number of checkpoints to store (default: 5)")
# Misc Parameters
tf.flags.DEFINE_boolean("allow_soft_placement", True, "Allow device soft device placement")
tf.flags.DEFINE_boolean("log_device_placement", False, "Log placement of ops on devices")
tf.flags.DEFINE_float("decay_coefficient", 2.5, "Decay coefficient (default: 2.5)")

FLAGS = tf.flags.FLAGS
# FLAGS._parse_flags()
# print("\nParameters:")
# for attr, value in sorted(FLAGS.__flags.items()):
#     print("{}={}".format(attr.upper(), value))
# print("")

class Trainer:
    def __init__(self, cfg):
        # load data
        self.cfg = cfg
        self.x_text = None
        self.y = None
        self.embedding_name = None
        self.embedding_dimension = None
        self.dataset_name = None
        # pre-process
        self.x_train = None
        self.y_train = None
        self.vocab_processor = None
        self.x_eval = None
        self.y_eval = None

    def load_data_and_labels(self):
        # Load data
        print("Loading embedding and dataset...")
        embedding_name = None
        dataset_name = self.cfg["datasets"]["default"]
        if FLAGS.enable_word_embeddings and self.cfg['word_embeddings']['default'] is not None:
            embedding_name = self.cfg['word_embeddings']['default']
            embedding_dimension = self.cfg['word_embeddings'][embedding_name]['dimension']
        else:
            embedding_dimension = FLAGS.embedding_dim
        datasets = None
        if dataset_name == "mrpolarity":
            datasets = data_helpers.get_datasets_mrpolarity(self.cfg["datasets"][dataset_name]["positive_data_file"]["path"],
                                                            self.cfg["datasets"][dataset_name]["negative_data_file"]["path"])
        elif dataset_name == "20newsgroup":
            datasets = data_helpers.get_datasets_20newsgroup(subset="train",
                                                             categories=self.cfg["datasets"][dataset_name]["categories"],
                                                             shuffle=self.cfg["datasets"][dataset_name]["shuffle"],
                                                             random_state=self.cfg["datasets"][dataset_name]["random_state"])
        elif dataset_name == "localdata":
            datasets = data_helpers.get_datasets_localdata(container_path=self.cfg["datasets"][dataset_name]["container_path"],
                                                           categories=self.cfg["datasets"][dataset_name]["categories"],
                                                           shuffle=self.cfg["datasets"][dataset_name]["shuffle"],
                                                           random_state=self.cfg["datasets"][dataset_name]["random_state"])
        print("Loaded dataset: {}. [Embedding: {}].\nLoading labels".format(dataset_name, embedding_name))
        x_text, y = data_helpers.load_data_labels(datasets)
        self.x_text = x_text
        self.y = y
        self.embedding_name = embedding_name
        self.embedding_dimension = embedding_dimension
        self.dataset_name = dataset_name
        print("Done load_data_and_labels()")

    def preprocess(self):
        # Data Preparation
        # ==================================================
        if self.dataset_name is None:
            self.load_data_and_labels()

        # Build vocabulary
        print("Build vocabulary")
        max_document_length = max([len(x.split(" ")) for x in self.x_text])
        vocab_processor = learn.preprocessing.VocabularyProcessor(max_document_length)
        x = np.array(list(vocab_processor.fit_transform(self.x_text)))

        # Randomly shuffle data
        print("Randomly shuffle data")
        np.random.seed(10)
        shuffle_indices = np.random.permutation(np.arange(len(self.y)))
        x_shuffled = x[shuffle_indices]
        y_shuffled = self.y[shuffle_indices]

        # Split train/test set
        print("Split train/test set")
        # TODO: This is very crude, should use cross-validation
        dev_sample_index = -1 * int(FLAGS.dev_sample_percentage * float(len(self.y)))
        x_train, x_eval = x_shuffled[:dev_sample_index], x_shuffled[dev_sample_index:]
        y_train, y_eval = y_shuffled[:dev_sample_index], y_shuffled[dev_sample_index:]

        del x, x_shuffled, y_shuffled
        del self.y

        print("Vocabulary Size: {:d}".format(len(vocab_processor.vocabulary_)))
        print("Train/Dev split: {:d}/{:d}".format(len(y_train), len(y_eval)))
        print("Train/Dev split: {:d}/{:d}".format(len(y_train), len(y_eval)))

        self.vocab_processor = vocab_processor
        self.x_train = x_train
        self.y_train = y_train
        self.x_eval = x_eval
        self.y_eval = y_eval
        print("Done preprocess()")

    def train(self):
        # Training
        # ==================================================
        if self.x_train is None:
            self.preprocess()

        with tf.Graph().as_default():
            session_conf = tf.ConfigProto(
              allow_soft_placement=FLAGS.allow_soft_placement,
              log_device_placement=FLAGS.log_device_placement)
            sess = tf.Session(config=session_conf)
            with sess.as_default():
                cnn = TextCNN(
                    sequence_length=self.x_train.shape[1],
                    num_classes=self.y_train.shape[1],
                    vocab_size=len(self.vocab_processor.vocabulary_),
                    embedding_size=self.embedding_dimension,
                    filter_sizes=list(map(int, FLAGS.filter_sizes.split(","))),
                    num_filters=FLAGS.num_filters,
                    l2_reg_lambda=FLAGS.l2_reg_lambda)

                # Define Training procedure
                print("Define Training procedure")
                global_step = tf.Variable(0, name="global_step", trainable=False)
                optimizer = tf.train.GradientDescentOptimizer(cnn.learning_rate)
                grads_and_vars = optimizer.compute_gradients(cnn.loss)
                train_op = optimizer.apply_gradients(grads_and_vars, global_step=global_step)

                # Keep track of gradient values and sparsity (optional)
                print("Keep track of gradient values and sparsity (optional)")
                grad_summaries = []
                for g, v in grads_and_vars:
                    if g is not None:
                        grad_hist_summary = tf.summary.histogram("{}/grad/hist".format(v.name), g)
                        sparsity_summary = tf.summary.scalar("{}/grad/sparsity".format(v.name), tf.nn.zero_fraction(g))
                        grad_summaries.append(grad_hist_summary)
                        grad_summaries.append(sparsity_summary)
                grad_summaries_merged = tf.summary.merge(grad_summaries)

                # Output directory for models and summaries
                print("Output directory for models and summaries")
                out_dir = os.path.abspath(os.path.join(os.path.curdir, "runs", self.dataset_name, str(self.embedding_name)))
                print("Writing to {}\n".format(out_dir))

                # Summaries for loss and accuracy
                loss_summary = tf.summary.scalar("loss", cnn.loss)
                acc_summary = tf.summary.scalar("accuracy", cnn.accuracy)

                # Train Summaries
                train_summary_op = tf.summary.merge([loss_summary, acc_summary, grad_summaries_merged])
                train_summary_dir = os.path.join(out_dir, "summaries", "train")
                train_summary_writer = tf.summary.FileWriter(train_summary_dir, sess.graph)

                # Dev summaries
                dev_summary_op = tf.summary.merge([loss_summary, acc_summary])
                dev_summary_dir = os.path.join(out_dir, "summaries", "dev")
                dev_summary_writer = tf.summary.FileWriter(dev_summary_dir, sess.graph)

                # Checkpoint directory. Tensorflow assumes this directory already exists so we need to create it
                checkpoint_dir = os.path.abspath(os.path.join(out_dir, "checkpoints"))
                checkpoint_prefix = os.path.join(checkpoint_dir, "model")
                if not os.path.exists(checkpoint_dir):
                    os.makedirs(checkpoint_dir)
                saver = tf.train.Saver(tf.global_variables(), max_to_keep=FLAGS.num_checkpoints)

                # Write vocabulary
                print("Write vocabulary")
                self.vocab_processor.save(os.path.join(out_dir, "vocab"))

                # Initialize all variables
                print("Initialize all variables")
                sess.run(tf.global_variables_initializer())
                if self.embedding_name is not None and self.cfg is not None:
                    vocabulary = self.vocab_processor.vocabulary_
                    initW = None
                    if self.embedding_name == 'word2vec':
                        # load embedding vectors from the word2vec
                        print("Load word2vec file {}".format(self.cfg['word_embeddings']['word2vec']['path']))
                        initW = data_helpers.load_embedding_vectors_word2vec(vocabulary,
                                                                             self.cfg['word_embeddings']['word2vec']['path'],
                                                                             self.cfg['word_embeddings']['word2vec']['binary'])
                        print("word2vec file has been loaded")
                    elif self.embedding_name == 'glove':
                        # load embedding vectors from the glove
                        print("Load glove file {}".format(self.cfg['word_embeddings']['glove']['path']))
                        initW = data_helpers.load_embedding_vectors_glove(vocabulary,
                                                                          self.cfg['word_embeddings']['glove']['path'],
                                                                          self.embedding_dimension)
                        print("glove file has been loaded\n")

                    if initW is not None:
                        sess.run(cnn.W.assign(initW))
                    else:
                        print("HIGH ALERT - cnn.W not assigned. initW is None\n")

                def train_step(x_batch, y_batch, learning_rate):
                    """
                    A single training step
                    """
                    feed_dict = {
                      cnn.input_x: x_batch,
                      cnn.input_y: y_batch,
                      cnn.dropout_keep_prob: FLAGS.dropout_keep_prob,
                      cnn.learning_rate: learning_rate
                    }
                    _, step, summaries, loss, accuracy = sess.run(
                        [train_op, global_step, train_summary_op, cnn.loss, cnn.accuracy],
                        feed_dict)
                    time_str = datetime.datetime.now().isoformat()
                    if step % 50 == 0:
                        print("{}: step {}, loss {:g}, acc {:g}, learning_rate {:g}"
                              .format(time_str, step, loss, accuracy, learning_rate))
                    train_summary_writer.add_summary(summaries, step)

                def dev_step(x_batch, y_batch, writer=None):
                    """
                    Evaluates model on a dev set
                    """
                    feed_dict = {
                      cnn.input_x: x_batch,
                      cnn.input_y: y_batch,
                      cnn.dropout_keep_prob: 1.0
                    }
                    step, summaries, loss, accuracy = sess.run(
                        [global_step, dev_summary_op, cnn.loss, cnn.accuracy],
                        feed_dict)
                    time_str = datetime.datetime.now().isoformat()
                    print("{}: step {}, loss {:g}, acc {:g}".format(time_str, step, loss, accuracy))
                    if writer:
                        writer.add_summary(summaries, step)

                def get_learning_rate(decay_speed, counter):
                    # # It uses dynamic learning rate with a high value at the beginning to speed up the training
                    # max_learning_rate = 0.005
                    # min_learning_rate = 0.0001
                    # learning_rate = min_learning_rate + (max_learning_rate - min_learning_rate) * 0.25 * math.exp(
                    #     -counter / decay_speed)
                    # # print("decay speed: {}. counter: {}. learning_rate: {}".format(decay_speed, counter, learning_rate))
                    # return learning_rate
                    return 0.0005


                # Generate batches
                print("Generate batches")
                batches = data_helpers.batch_iter(
                    list(zip(self.x_train, self.y_train)), FLAGS.batch_size, FLAGS.num_epochs)

                decay_speed = FLAGS.decay_coefficient * len(self.y_train) / FLAGS.batch_size

                # Training loop. For each batch...
                print("Training loop. For each batch...")
                counter = 0
                for batch in batches:
                    learning_rate = get_learning_rate(decay_speed, counter)
                    counter += 1
                    x_batch, y_batch = zip(*batch)
                    train_step(x_batch, y_batch, learning_rate)
                    current_step = tf.train.global_step(sess, global_step)
                    if current_step % FLAGS.evaluate_every == 0:
                        print("\nEvaluation:")
                        dev_step(self.x_eval, self.y_eval, writer=dev_summary_writer)
                        print()
                    if current_step % FLAGS.checkpoint_every == 0:
                        path = saver.save(sess, checkpoint_prefix, global_step=current_step)
                        print("\tSaved model checkpoint to {}\n".format(path))

                print("End training. counter: {}. batch size: {}\n".format(counter, FLAGS.batch_size))


def main(argv=None):
    print("Loading config file...")
    with open("config.yml", 'r') as ymlfile:
        cfg = yaml.load(ymlfile)

    print("Start training...")
    t = Trainer(cfg)
    t.train()


if __name__ == '__main__':
    os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
    tf.app.run()
