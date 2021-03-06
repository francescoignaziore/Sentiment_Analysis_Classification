"""Here the tensorflow input pipeline is built.
ABOUT THE TweetDataset
---------------------------
The TweetDataset class implements the whole data pipeline (from text file to network input)
for a tensorflow model.
USAGE:
- To create a new dataset you need to specify the input files to read, and a vocabulary to use.
    The Dataset class will read the files line by line and create a dataset out of it.
    If you want to create a training or testing dataset (for which you have labels), then you
    should also pass a labels list as shown below.
        # build the dataset
        my_dataset = TweetDataset(input_files=[file1.txt, file2.txt],
                                    vocabulary="vocab.pkl",
                                    labels=[0,1], # 0 for the first file, 1 for the second
                                    # additional parameters
                                     batch_size=batch_size,
                                     buffer_size=buffer_size,
                                     max_len=100,
                                     validation_size=validation_size)
        # and finally train the net
        net.train(my_dataset, **train_params)
    Note: if you want to use the dataset for prediction you just need to set labels to None.
- To add a preprocessing step in the pipeline just add a function to this class that workds
    with tf.data.Dataset objects like the *encode_text* function.
    Note: by default the text is now encoded, i.e. each string token is transformed into a vocabulary index.
    To keep the data as text add a check to avoid using the encode_text function.
"""

from transformers import BertTokenizer, TFBertModel, AutoTokenizer, BertJapaneseTokenizer, \
    TFBertForSequenceClassification
import tensorflow as tf
import tensorflow_datasets as tfds
from preprocessing import standard_vocab_name
from preprocessing.pipeline import get_vocabulary
import data
import os


class TweetDataset():
    "Wrapper class for tf.data.Dataset object"

    def __init__(self, input_files,
                 labels=None,
                 batch_size=12,
                 buffer_size=1000,
                 vocabulary=None,
                 max_len=100,
                 validation_size=0.2,
                 test_size=0.2,
                 encode_text=False,
                 bert_embed=True,
                 do_padding=False,
                 caching=False,
                 size = data.full_dimension):
        """
        :param encode_text: (bool)
        :param max_len: maximum length of the input sequence
            :type max_len: int
        :param validation_size
            :type validation_size: float
        :param input_files: list of input files to use.
            :type input_files: list(str)
        :param batch_size
            :type batch_size: int
        :param buffer_size:
            :type buffer_size: int
            More on buffer_size.
            We designed the Dataset.shuffle() transformation to handle datasets
            that are too large to fit in memory. Instead of shuffling the entire dataset,
            it maintains a buffer of buffer_size elements, and randomly selects the next
            element from that buffer (replacing it with the next input element,
            if one is available).
        """
        self.input_files = input_files
        self.labels = labels
        self.buffer_size = buffer_size
        self.size = size
        self.dataset = self.load_dataset(labels, bert=bert_embed)
        self.do_padding = do_padding
        self.steps_per_epoch = None
        self.valid_steps_per_epoch = None
        if encode_text and not bert_embed:
            if not vocabulary: vocabulary = standard_vocab_name
            self.vocab = get_vocabulary(vocabulary)
            self.encode_text(self.vocab)

        if self.labels:
            self.train, self.validate, self.test = self.split_dataset(batch_size, validation_size,
                                                                      test_size, max_len)
            if caching:
                print("Caching.")
                abs_path = os.path.abspath(os.path.dirname(__file__))
                paths = [os.path.join(abs_path, f) for f in
                         ["../data/train_data", "../data/valid_data", "../data/test_data"]]
                self.train = self.train.cache(paths[0])
                self.validate = self.validate.cache(paths[1])
                self.test = self.test.cache(paths[2])
        else:
            if self.do_padding:
                self.dataset = self.dataset.padded_batch(batch_size, padded_shapes=[max_len])
            self.pred_data = self.dataset.shuffle(self.size)

    @tf.function
    def get_bert_encoding(self, sentence):
        """Doing the real Bert job"""
        encoded_input = self.tokenizer.encode_plus(str(sentence),
                                                   max_length=50,
                                                   return_tensors='tf',
                                                   add_special_tokens=True,
                                                   return_token_type_ids=False,
                                                   pad_to_max_length=True,
                                                   return_attention_mask=True)
        return encoded_input["input_ids"], encoded_input["attention_mask"]

    def load_dataset(self, labels, bert=True):
        """
        :param bert: whether to encode the input using bert --> output shape = [1,768],[2]
        :param labels: list of labels.
            :type labels: list(int)
            If labels=None the dataset will be loaded in test mode.
            If labels!= None the length of labels has to match the length of the input_files list.
        :return:
        """
        print("Loading the dataset.")
        ## READING TEXT FILES
        abs_path = os.path.abspath(os.path.dirname(__file__))
        paths = [os.path.join(abs_path, f) for f in self.input_files]
        datasets = []
        for path in paths:
            print("Loading ",path)
            datasets.append(tf.data.TextLineDataset(path).take(self.size//len(paths)))
        ## EMBEDDING TO BERT (if requested)
        if bert:
            print("Applying Bert embedding to the dataset.")
            self.tokenizer = AutoTokenizer.from_pretrained('bert-base-cased')
            self.trans_model = TFBertModel.from_pretrained('bert-base-cased')
            def tf_encode(sentence):
                result_ids, result_masks = tf.py_function(func=self.get_bert_encoding,
                                                          inp=[sentence], Tout=(tf.int32,tf.int32))
                result_ids.set_shape([1,50])
                result_masks.set_shape([1,50])
                return (result_ids,result_masks)
            for i in range(len(datasets)):
               datasets[i] = datasets[i].map(tf_encode)
        ## ADDING LABELS (if requested)
        if labels:
            assert len(labels) == len(self.input_files)
            for i in range(len(datasets)):
                if bert: datasets[i] = datasets[i].map(lambda ids,mask: ((ids,mask),tf.one_hot(labels[i], 2)))
                else: datasets[i] = datasets[i].map(lambda line: (line,tf.one_hot(labels[i], 2)))
        ##MERGING the datasets
        dataset_final = datasets[0]
        for i in range(len(datasets)-1):
            dataset_final = dataset_final.concatenate(datasets[i+1])

        if not labels: dataset_final = dataset_final.map(lambda line: (line))
        return dataset_final.shuffle(buffer_size=self.size)

    def encode_text(self, vocabulary):
        """Helper function to transform string tokens into integers."""
        print("Encoding the dataset.")
        vocabulary_set = set(vocabulary.keys())
        encoder = tfds.features.text.TokenTextEncoder(vocabulary_set)

        # We want to use Dataset.map to apply this function to each element
        # of the dataset. Dataset.map runs in graph mode so you can't .map
        # this function directly: You need to wrap it in a tf.py_function.
        # The tf.py_function will pass regular tensors
        # (with a value and a .numpy() method to access it),
        # to the wrapped python function.

        # py_func doesn't set the shape of the returned tensors.
        # More on py function:
        # This function allows expressing computations in a TensorFlow graph
        # as Python functions. In particular, it wraps a Python function func
        # in a once-differentiable TensorFlow operation that executes
        # it with eager execution enabled.
        def encode_map_fn(text, label):
            encoded_text, label = tf.py_function(lambda t, l: (encoder.encode(t.numpy()), l),
                                                 inp=[text, label], Tout=(tf.int64, tf.float32))
            encoded_text.set_shape([None])
            label.set_shape([])
            return encoded_text, label

        def encode_map_fn_no_label(text):
            encoded_text, label = tf.py_function(lambda t: encoder.encode(t.numpy()),
                                                 inp=[text], Tout=tf.int64)
            encoded_text.set_shape([None])
            label.set_shape([])
            return encoded_text

        # finally applying the transformation word->idx on the dataset
        if self.labels:
            self.dataset = self.dataset.map(encode_map_fn)
        else:
            self.dataset = self.dataset.map(encode_map_fn_no_label)

    def split_dataset(self, batch_size, validation_size, test_size, max_len):
        """Splits the dataset in training and validation."""
        print("Splitting dataset.")
        # First calculating the splits
        print("Batching and shuffling")
        self.dataset = self.dataset.batch(batch_size)
        validation_split = int(validation_size*self.size/batch_size) # number of batched in validation
        test_split = int(test_size*self.size/batch_size) # number of batches in test
        train_split = int(self.size/batch_size) - validation_split - test_split # number of batches in train
        print("Training on ",train_split,", validating on ",validation_split,", testing on ",test_split)
        # TRAIN
        # batching and shuffling
        self.steps_per_epoch = train_split
        train_data = self.dataset.skip(validation_split + test_split)
        if self.do_padding: train_data = train_data.padded_batch(batch_size, padded_shapes=([max_len], [1]))
        # VALID
        validation_data = self.dataset.take(validation_split)
        self.valid_steps_per_epoch = validation_split
        if self.do_padding: validation_data = validation_data.padded_batch(batch_size, padded_shapes=([max_len], [1]))
        # TEST
        test_data = self.dataset.skip(validation_split).take(test_split)
        if self.do_padding: test_data = test_data.padded_batch(batch_size, padded_shapes=([max_len], [1]))

        return train_data.prefetch(self.buffer_size), \
               validation_data.prefetch(self.buffer_size), \
               test_data.prefetch(self.buffer_size)


class TweetDatasetGenerator():
    "Builds a generator for the tweet dataset"

    def __init__(self,input_files,
                 labels=None,
                 batch_size=12,
                 max_len=50,
                 train_p=0.3,
                 valid_p=0.1,
                 test_p=0.05,
                 size = data.full_dimension):
        # --------- Parameters here
        assert(size<data.full_dimension), "The size is too big"
        self.size = size
        self.max_len=max_len
        # these are Bert parameters
        self.tokenizer = AutoTokenizer.from_pretrained('bert-base-cased')
        self.trans_model = TFBertModel.from_pretrained('bert-base-cased')
        # ---------------------------

        ## load the dataset first
        x,y = self.load_data_from_files(input_files, labels)
        x,y = self.preprocess_xy(x,y)
        train, val, test = \
            self.split_data(x,y,train_p,valid_p,test_p, batch_size)
        self.train = self.batch_and_prepare(train, is_training=True, batch_size=batch_size)
        self.valid= self.batch_and_prepare(val, is_training=False, batch_size=batch_size)
        self.test = self.batch_and_prepare(test, is_training=False, batch_size=batch_size)


    def load_data_from_files(self,files,labels):
        ## loading and merging the files ----------
        print("Loading from files.")
        abs_path = os.path.abspath(os.path.dirname(__file__))
        paths = [os.path.join(abs_path, f) for f in files]
        # 1. Loading the text
        # 2. Building the labels
        datasets = []
        dataset_labels = []
        length = self.size//len(paths)
        for i,path in enumerate(paths):
            print("Loading ", path)
            datasets.append(tf.data.TextLineDataset(path))
            dataset_labels.append(tf.data.Dataset.from_tensor_slices([labels[i]]*length))
        # 3. MERGING the datasets
        dataset_final = datasets[0]
        dataset_final_labels = dataset_labels[0]
        for i in range(len(datasets) - 1):
            dataset_final = dataset_final.concatenate(datasets[i + 1])
            dataset_final_labels = dataset_final_labels.concatenate(dataset_labels[i + 1])
        # 4. shuffling
        dataset_final = dataset_final.shuffle(buffer_size=length, seed=0).cache(os.path.join(abs_path,"../data/x"))
        dataset_final_labels = dataset_final_labels.shuffle(buffer_size=length, seed=0).cache(os.path.join(abs_path,"../data/y"))
        return dataset_final, dataset_final_labels
        ## ----------------------

    def get_bert_encoding(self, sentence):
        """Doing the real Bert job"""
        encoded_input = self.tokenizer.encode_plus(str(sentence),
                                                   max_length=self.max_len,
                                                   return_tensors='tf',
                                                   add_special_tokens=True,
                                                   return_token_type_ids=False,
                                                   pad_to_max_length=True,
                                                   return_attention_mask=True)
        return encoded_input["input_ids"], encoded_input["attention_mask"]

    def preprocess_xy(self, x,y):
        print("Preprocessing text and labels.")
        # helper function that hides the python logic to encode the labels
        def tf_yencode(label):
            y_encoded = tf.py_function(lambda lbl: tf.one_hot(lbl,2), inp=[label], Tout=tf.float32)
            y_encoded.set_shape([2])
            return y_encoded

        y = y.map(tf_yencode, num_parallel_calls=4)

        def tf_encode(sentence):
            result_ids, result_masks = tf.py_function(func=self.get_bert_encoding,
                                                      inp=[sentence], Tout=(tf.int32, tf.int32))
            result_ids.set_shape([1, 50])
            result_masks.set_shape([1, 50])
            return (result_ids, result_masks)

        x = x.map(tf_encode, num_parallel_calls=4)
        return x,y

    def split_data(self, x, y, train_p, valid_p, test_p, batch_size):
        print("Splitting.")
        train_size = int(self.size*train_p)
        valid_size = int(self.size*valid_p)
        test_size = int(self.size*test_p)
        self.train_size, self.valid_size, self.test_size = train_size//batch_size, \
                                                           valid_size//batch_size, \
                                                           test_size//batch_size
        print("Train: ",train_size,". Valid: ",valid_size,". Test: ",test_size)
        dataset = tf.data.Dataset.zip((x,y))
        train = dataset.take(train_size)
        valid = dataset.skip(train_size).take(valid_size)
        test = dataset.skip(train_size+valid_size).take(test_size)
        return train,valid,test

    @staticmethod
    def batch_and_prepare(dataset,is_training,batch_size):
        dataset = dataset.batch(batch_size, drop_remainder=is_training)
        dataset = dataset.repeat()
        return dataset



