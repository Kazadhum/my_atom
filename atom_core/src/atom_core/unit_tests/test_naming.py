import unittest
from naming import generateName, generateKey, generateLabeledTopic

class TestGenerateName(unittest.TestCase):

    def test_generate_name_from_strings(self):
        prefix_str = 'prefix'
        suffix_str = 'suffix'
        separator = 'x'
        name = 'name'

        self.assertEqual(generateName(name, prefix_str, suffix_str, separator), 'prefixxnamexsuffix')

    def test_generate_name_raises_type_error_with_non_strings(self):
        prefix_int = 1
        suffix_int = 3
        separator = '_'
        name = 2

        with self.assertRaises(TypeError):
            generateName(name, prefix_int, suffix_int, separator)

class TestGenerateKey(unittest.TestCase):

    def test_generate_key_result(self):
        parent_str = 'parent'
        child_str = 'child'
        suffix_str = '.key'

        self.assertEqual(generateKey(parent_str, child_str, suffix_str), 'parent-child.key')

    def test_generate_key_raises_type_error_with_non_strings(self):
        parent_int = 12
        child_int = 34
        suffix_str = '.key'

        with self.assertRaises(TypeError):
            generateKey(parent_int, child_int, suffix_str)

class GenerateLabeledTopic(unittest.TestCase):

    def test_generate_labeled_topic_results_without_collection_key(self):

        type = '2d'
        topic = '/laser_scan'
        suffix = '/suffix'

        self.assertEqual(generateLabeledTopic(topic=topic, collection_key=None, type=type, suffix=suffix), '/laser_scan/labeled2d/suffix')

    def test_generate_labeled_topic_results_with_collection_key(self):

        type = '3d'
        topic = '/laser_scan'
        suffix = '/suffix'
        collection_key = '/my_collection'

        self.assertEqual(generateLabeledTopic(topic=topic, collection_key=collection_key, type=type, suffix=suffix), '/laser_scan/c/my_collection/labeled3d/suffix')

    def test_generate_labeled_topic_raises_assert_error_with_invalid_type_string(self):

        type = 'wrong_type'
        topic = '/laser_scan'
        suffix = '/suffix'
        collection_key = '/my_collection'

        with self.assertRaises(AssertionError):
            generateLabeledTopic(topic=topic, collection_key=collection_key, type=type, suffix=suffix)

    def test_generate_labeled_topic_raises_type_error_with_non_strings(self):

        type = '2d'
        topic = True
        suffix = 5
        collection_key = 10

        with self.assertRaises(TypeError):
            generateLabeledTopic(topic=topic, collection_key=collection_key, type=type, suffix=suffix)