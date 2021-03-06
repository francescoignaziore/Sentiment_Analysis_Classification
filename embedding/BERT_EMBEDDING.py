import tensorflow_hub as hub
import tensorflow as tf
import bert
FullTokenizer = bert.bert_tokenization.FullTokenizer
from tensorflow.keras.models import Model       # Keras is the new high level API for TensorFlow
import math
import numpy as np


def get_masks(tokens, max_seq_length):
    """Mask for padding"""
    if len(tokens)>max_seq_length:
        raise IndexError("Token length more than max seq length!")
    return [1]*len(tokens) + [0] * (max_seq_length - len(tokens))


def get_segments(tokens, max_seq_length):
    """Segments: 0 for the first sequence, 1 for the second"""
    if len(tokens)>max_seq_length:
        raise IndexError("Token length more than max seq length!")
    segments = []
    current_segment_id = 0
    for token in tokens:
        segments.append(current_segment_id)
        if token == "[SEP]":
            current_segment_id = 1
    return segments + [0] * (max_seq_length - len(tokens))


def get_ids(tokens, tokenizer, max_seq_length):
    """Token ids from Tokenizer vocab"""
    token_ids = tokenizer.convert_tokens_to_ids(tokens)
    input_ids = token_ids + [0] * (max_seq_length-len(token_ids))
    return input_ids

#the embedding dimension of a tweet(sentence) is standard 768

def get_BERT_EMBEDDING(input_files, output_location, max_seq_length=128):
    out_dim = 768
    #the total number of tweets
    N = 20000
    output = np.zeros((N, out_dim))

    ## FIRST DEFINE THE MODEL
    print("Building Bert model.")
    input_word_ids = tf.keras.layers.Input(shape=(max_seq_length,), dtype=tf.int32, name="input_word_ids")
    input_mask = tf.keras.layers.Input(shape=(max_seq_length,), dtype=tf.int32, name="input_mask")
    segment_ids = tf.keras.layers.Input(shape=(max_seq_length,), dtype=tf.int32, name="segment_ids")
    bert_layer = hub.KerasLayer("https://tfhub.dev/tensorflow/bert_en_uncased_L-12_H-768_A-12/1", trainable=True)
    pooled_output, sequence_output = bert_layer([input_word_ids, input_mask, segment_ids])
    model = Model(inputs=[input_word_ids, input_mask, segment_ids],
                  outputs=[pooled_output, sequence_output])

    ## THEN PROCESS THE FILES
    print("Processing the files")
    for i, file in enumerate(input_files):
        with open(file) as f:
            for l, line in enumerate(f):
                vocab_file = bert_layer.resolved_object.vocab_file.asset_path.numpy()
                do_lower_case = bert_layer.resolved_object.do_lower_case.numpy()
                tokenizer = FullTokenizer(vocab_file, do_lower_case)
                stokens = tokenizer.tokenize(line)
                stokens = ["[CLS]"] + stokens + ["[SEP]"]
                #get the model inputs from the tokens
                input_ids = get_ids(stokens, tokenizer, max_seq_length)
                input_masks = get_masks(stokens, max_seq_length)
                input_segments = get_segments(stokens, max_seq_length)
                pool_embs, all_embs = model.predict([np.array(input_ids).reshape(1,-1),
                            np.array(input_masks).reshape(1,-1), np.array(input_segments).reshape(1,-1)])
                #now we need to store this into a matrix
                #look at how we did this with the embedding matrix
                #output dim is also 768 then
                output[i, :] = pool_embs
                if l % 10000 == 0:
                    print(l)
    np.savez(output_location, output)
    return output












