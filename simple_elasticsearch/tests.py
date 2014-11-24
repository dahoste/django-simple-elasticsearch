import copy
from datadiff import tools as ddtools
from django import forms
from django.test import TestCase
from elasticsearch import Elasticsearch
from elasticsearch_dsl import Search
from elasticsearch_dsl.result import Response
import mock
from simple_elasticsearch.forms import ESSearchForm, ESSearchProcessor

try:
    # `reload` is not a python3 builtin like python2
    reload
except NameError:
    from imp import reload

from . import settings as es_settings
from .mixins import ElasticsearchIndexMixin
from .models import Blog, BlogPost


class ElasticsearchIndexMixinClass(ElasticsearchIndexMixin):
    pass


class BlogPostSearchForm(ESSearchForm):
    q = forms.CharField()

    def get_index(self):
        return 'blog'

    def get_type(self):
        return 'posts'

    def prepare_query(self):
        return {
            "query": {
                "match_all": {}
            }
        }


class ElasticsearchIndexMixinTestCase(TestCase):

    @property
    def latest_post(self):
        return BlogPost.objects.select_related('blog').latest('id')

    @mock.patch('simple_elasticsearch.mixins.Elasticsearch.delete')
    @mock.patch('simple_elasticsearch.mixins.Elasticsearch.index')
    def setUp(self, mock_index, mock_delete):
        self.blog = Blog.objects.create(
            name='test blog name',
            description='test blog description'
        )

        # hack the return value to ensure we save some BlogPosts here;
        # without this mock, the post_save handler indexing blows up
        # as there is no real ES instance running
        mock_index.return_value = mock_delete.return_value = {}

        post = BlogPost.objects.create(
            blog=self.blog,
            title="DO-NOT-INDEX title",
            slug="DO-NOT-INDEX",
            body="DO-NOT-INDEX body"
        )

        for x in range(1, 10):
            BlogPost.objects.create(
                blog=self.blog,
                title="blog post title {0}".format(x),
                slug="blog-post-title-{0}".format(x),
                body="blog post body {0}".format(x)
            )

    def test__get_es__with_default_settings(self):
        result = BlogPost.get_es()
        self.assertIsInstance(result, Elasticsearch)
        self.assertEqual(result.transport.hosts[0]['host'], '127.0.0.1')
        self.assertEqual(result.transport.hosts[0]['port'], 9200)

    def test__get_es__with_custom_server(self):
        # include a custom class here as the internal `_es` is cached, so can't reuse the
        # `ElasticsearchIndexClassDefaults` global class (see above).
        class ElasticsearchIndexClassCustomSettings(ElasticsearchIndexMixin):
            pass

        with self.settings(ELASTICSEARCH_SERVER=['search.example.com:9201']):
            reload(es_settings)
            result = ElasticsearchIndexClassCustomSettings.get_es()
            self.assertIsInstance(result, Elasticsearch)
            self.assertEqual(result.transport.hosts[0]['host'], 'search.example.com')
            self.assertEqual(result.transport.hosts[0]['port'], 9201)

        reload(es_settings)

    def test__get_es__with_custom_connection_settings(self):
        # include a custom class here as the internal `_es` is cached, so can't reuse the
        # `ElasticsearchIndexClassDefaults` global class (see above).
        class ElasticsearchIndexClassCustomSettings(ElasticsearchIndexMixin):
            pass

        with self.settings(ELASTICSEARCH_CONNECTION_PARAMS={'hosts': ['search2.example.com:9202'], 'sniffer_timeout': 15}):
            reload(es_settings)
            result = ElasticsearchIndexClassCustomSettings.get_es()
            self.assertIsInstance(result, Elasticsearch)
            self.assertEqual(result.transport.hosts[0]['host'], 'search2.example.com')
            self.assertEqual(result.transport.hosts[0]['port'], 9202)
            self.assertEqual(result.transport.sniffer_timeout, 15)
        reload(es_settings)

    @mock.patch('simple_elasticsearch.mixins.ElasticsearchIndexMixin.index_add_or_delete')
    def test__save_handler(self, mock_index_add_or_delete):
        # with a create call
        post = BlogPost.objects.create(
            blog=self.blog,
            title="blog post title foo",
            slug="blog-post-title-foo",
            body="blog post body foo"
        )
        mock_index_add_or_delete.assert_called_with(post)
        mock_index_add_or_delete.reset_mock()

        # with a plain save call
        post.save()
        mock_index_add_or_delete.assert_called_with(post)

    @mock.patch('simple_elasticsearch.mixins.ElasticsearchIndexMixin.index_delete')
    def test__delete_handler(self, mock_index_delete):
        post = self.latest_post
        post.delete()
        mock_index_delete.assert_called_with(post)

    @mock.patch('simple_elasticsearch.mixins.Elasticsearch.index')
    def test__index_add(self, mock_index):
        post = self.latest_post
        mock_index.return_value = {}

        # make sure an invalid object passed in returns False
        result = BlogPost.index_add(None)
        self.assertFalse(result)

        # make sure indexing an item calls Elasticsearch.index() with
        # the correct variables, with normal index name
        result = BlogPost.index_add(post)
        self.assertTrue(result)
        mock_index.assert_called_with('blog', 'posts', BlogPost.get_document(post), post.pk)

        # make sure indexing an item calls Elasticsearch.index() with
        # the correct variables, with non-standard index name
        result = BlogPost.index_add(post, 'foo')
        self.assertTrue(result)
        mock_index.assert_called_with('foo', 'posts', BlogPost.get_document(post), post.pk)

        # this one should not index (return false) because the
        # 'should_index' for this post should make it skip it
        post = BlogPost.objects.get(slug="DO-NOT-INDEX")
        result = BlogPost.index_add(post)
        self.assertFalse(result)

    @mock.patch('simple_elasticsearch.mixins.Elasticsearch.delete')
    def test__index_delete(self, mock_delete):
        post = self.latest_post
        mock_delete.return_value = {
            "acknowledged": True
        }

        # make sure an invalid object passed in returns False
        result = BlogPost.index_delete(None)
        self.assertFalse(result)

        # make sure deleting an item calls Elasticsearch.delete() with
        # the correct variables, with normal index name
        result = BlogPost.index_delete(post)
        self.assertTrue(result)
        mock_delete.assert_called_with('blog', 'posts', post.pk)

        # make sure deleting an item calls Elasticsearch.delete() with
        # the correct variables, with non-standard index name
        result = BlogPost.index_delete(post, 'foo')
        self.assertTrue(result)
        mock_delete.assert_called_with('foo', 'posts', post.pk)

    @mock.patch('simple_elasticsearch.mixins.ElasticsearchIndexMixin.index_add')
    @mock.patch('simple_elasticsearch.mixins.ElasticsearchIndexMixin.index_delete')
    def test__index_add_or_delete(self, mock_index_delete, mock_index_add):
        # invalid object passed in, should return False
        result = BlogPost.index_add_or_delete(None)
        self.assertFalse(result)

        # this one should not index (return false) because the
        # `should_index` for this post should make it skip it;
        # `index_delete` should get called
        mock_index_delete.return_value = True
        post = BlogPost.objects.get(slug="DO-NOT-INDEX")

        result = BlogPost.index_add_or_delete(post)
        self.assertTrue(result)
        mock_index_delete.assert_called_with(post, '')

        result = BlogPost.index_add_or_delete(post, 'foo')
        self.assertTrue(result)
        mock_index_delete.assert_called_with(post, 'foo')

        # `index_add` call results below
        mock_index_add.return_value = True
        post = self.latest_post

        result = BlogPost.index_add_or_delete(post)
        self.assertTrue(result)
        mock_index_add.assert_called_with(post, '')

        result = BlogPost.index_add_or_delete(post, 'foo')
        self.assertTrue(result)
        mock_index_add.assert_called_with(post, 'foo')

    def test__get_index_name(self):
        self.assertEqual(BlogPost.get_index_name(), 'blog')

    def test__get_type_name(self):
        self.assertEqual(BlogPost.get_type_name(), 'posts')

    def test__get_queryset(self):
        queryset = BlogPost.objects.all().select_related('blog').order_by('pk')
        self.assertEqual(list(BlogPost.get_queryset().order_by('pk')), list(queryset))

    def test__get_index_name_notimplemented(self):
        with self.assertRaises(NotImplementedError):
            ElasticsearchIndexMixinClass.get_index_name()

    def test__get_type_name_notimplemented(self):
        with self.assertRaises(NotImplementedError):
            ElasticsearchIndexMixinClass.get_type_name()

    def test__get_queryset_notimplemented(self):
        with self.assertRaises(NotImplementedError):
            ElasticsearchIndexMixinClass.get_queryset()

    def test__get_type_mapping(self):
        mapping = {
            "properties": {
                "created_at": {
                    "type": "date",
                    "format": "dateOptionalTime"
                },
                "title": {
                    "type": "string"
                },
                "body": {
                    "type": "string"
                },
                "slug": {
                    "type": "string"
                },
                "blog": {
                    "properties": {
                        "id": {
                            "type": "long"
                        },
                        "name": {
                            "type": "string"
                        },
                        "description": {
                            "type": "string"
                        }
                    }
                }
            }
        }
        self.assertEqual(BlogPost.get_type_mapping(), mapping)

    def test__get_type_mapping_notimplemented(self):
        self.assertEqual(ElasticsearchIndexMixinClass.get_type_mapping(), {})

    def test__get_request_params(self):
        post = self.latest_post
        # TODO: implement the method to test it works properly
        self.assertEqual(BlogPost.get_request_params(post), {})

    def test__get_request_params_notimplemented(self):
        self.assertEqual(ElasticsearchIndexMixinClass.get_request_params(1), {})

    def test__get_bulk_index_limit(self):
        self.assertTrue(str(BlogPost.get_bulk_index_limit()).isdigit())

    def test__get_query_limit(self):
        self.assertTrue(str(BlogPost.get_query_limit()).isdigit())

    def test__get_document_id(self):
        post = self.latest_post
        result = BlogPost.get_document_id(post)
        self.assertEqual(result, post.pk)

    def test__get_document(self):
        post = self.latest_post
        result = BlogPost.get_document(post)
        self.assertEqual(result, {
            'title': post.title,
            'slug': post.slug,
            'blog': {
                'id': post.blog.pk,
                'description': post.blog.description,
                'name': post.blog.name
            },
            'created_at': post.created_at,
            'body': post.body
        })

    def test__get_document_notimplemented(self):
        with self.assertRaises(NotImplementedError):
            ElasticsearchIndexMixinClass.get_document(1)

    @mock.patch('simple_elasticsearch.mixins.Elasticsearch.index')
    def test__should_index(self, mock_index):
        post = self.latest_post
        self.assertTrue(BlogPost.should_index(post))

        mock_index.return_value = {}
        post = BlogPost.objects.get(slug="DO-NOT-INDEX")
        self.assertFalse(BlogPost.should_index(post))

    def test__should_index_notimplemented(self):
        self.assertTrue(ElasticsearchIndexMixinClass.should_index(1))

    @mock.patch('simple_elasticsearch.mixins.queryset_iterator')
    def test__bulk_index_queryset(self, mock_queryset_iterator):
        queryset = BlogPost.get_queryset().exclude(slug='DO-NOT-INDEX')
        BlogPost.bulk_index(queryset=queryset)
        mock_queryset_iterator.assert_called_with(queryset, BlogPost.get_query_limit())

        mock_queryset_iterator.reset_mock()

        queryset = BlogPost.get_queryset()
        BlogPost.bulk_index()
        # to compare QuerySets, they must first be converted to lists.
        self.assertEqual(list(mock_queryset_iterator.call_args[0][0]), list(queryset))

    @mock.patch('simple_elasticsearch.models.BlogPost.get_document')
    @mock.patch('simple_elasticsearch.models.BlogPost.should_index')
    @mock.patch('simple_elasticsearch.mixins.Elasticsearch.bulk')
    def test__bulk_index_should_index(self, mock_bulk, mock_should_index, mock_get_document):
        # hack the return value to ensure we save some BlogPosts here;
        # without this mock, the post_save handler indexing blows up
        # as there is no real ES instance running
        mock_bulk.return_value = {}

        queryset_count = BlogPost.get_queryset().count()
        BlogPost.bulk_index()
        self.assertTrue(mock_should_index.call_count == queryset_count)

    @mock.patch('simple_elasticsearch.models.BlogPost.get_document')
    @mock.patch('simple_elasticsearch.mixins.Elasticsearch.bulk')
    def test__bulk_index_get_document(self, mock_bulk, mock_get_document):
        mock_bulk.return_value = mock_get_document.return_value = {}

        queryset_count = BlogPost.get_queryset().count()
        BlogPost.bulk_index()

        # One of the items is not meant to be indexed (slug='DO-NOT-INDEX'), so the
        # get_document function will get called one less time due to this.
        self.assertTrue(mock_get_document.call_count == (queryset_count - 1))

        # figure out how many times es.bulk() should get called in the
        # .bulk_index() method and verify it's the same
        bulk_times = int(queryset_count / BlogPost.get_bulk_index_limit()) + 1
        self.assertTrue(mock_bulk.call_count == bulk_times)


class ESSearchFormTestCase(TestCase):

    def setUp(self):
        self.query = {'q': 'python'}
        self.form = BlogPostSearchForm(self.query)
        self.form.is_valid()

    def test__form_get_index(self):
        self.assertEqual(self.form.get_index(), 'blog')

    def test__form_get_type(self):
        self.assertEqual(self.form.get_type(), 'posts')

    def test__form_query_params(self):
        self.assertEqual(self.form.query_params, {})

        query_params = {'test': 'foo'}
        form = BlogPostSearchForm(query_params=query_params)
        self.assertEqual(form.query_params, query_params)

    @mock.patch('simple_elasticsearch.forms.ESSearchProcessor.search')
    @mock.patch('simple_elasticsearch.forms.ESSearchProcessor.add_search')
    @mock.patch('simple_elasticsearch.forms.ESSearchProcessor.__init__')
    def test__form_es(self, mock_esp_init, mock_esp_add_search, mock_esp_search):
        # __init__ methods always return None
        mock_esp_init.return_value = None

        # this allows the form.search() method to complete without
        # exceptions being raised
        mock_esp_search.return_value = [Response({})]

        # by default, the form has no internal Elasticsearch object
        self.assertEqual(self.form.es, None)

        # on search(), ESSearchProcessor() should be initialized with
        # a None Elasticsearch object (the form's)
        self.form.search()
        mock_esp_init.assert_called_with(None)
        mock_esp_init.reset()

        # here, we're setting the form's internal Elasticsearch
        # object, so the above tests should have the opposite
        # result
        form = BlogPostSearchForm(es=Elasticsearch(['127.0.0.2:9201']))
        self.assertIsInstance(form.es, Elasticsearch)
        self.assertEqual(form.es.transport.hosts[0]['host'], '127.0.0.2')
        self.assertEqual(form.es.transport.hosts[0]['port'], 9201)

        form.search()
        mock_esp_init.assert_called_with(form.es)

    def test__form_data_validation(self):
        form = BlogPostSearchForm({})
        self.assertFalse(form.is_valid())

        form = BlogPostSearchForm({'q': ''})
        self.assertFalse(form.is_valid())

        form = BlogPostSearchForm({'q': 'foo'})
        self.assertTrue(form.is_valid())

    @mock.patch('simple_elasticsearch.forms.ESSearchProcessor.search')
    @mock.patch('simple_elasticsearch.forms.ESSearchProcessor.add_search')
    def test__form_search(self, mock_esp_add_search, mock_esp_search):
        mock_esp_search.return_value = [Response({})]
        self.form.search()
        mock_esp_add_search.assert_called_with(self.form, 1, 20)
        mock_esp_add_search.reset()

        self.form.search(5, 50)
        mock_esp_add_search.assert_called_with(self.form, 5, 50)


class ESSearchProcessorTestCase(TestCase):

    def setUp(self):
        self.query = {'q': 'python'}
        self.form = BlogPostSearchForm(self.query)
        self.form.is_valid()

    def test__esp_reset(self):
        esp = ESSearchProcessor()

        self.assertTrue(len(esp.bulk_search_data) == 0)
        self.assertTrue(len(esp.page_ranges) == 0)

        esp.add_search(self.form)

        self.assertFalse(len(esp.bulk_search_data) == 0)
        self.assertFalse(len(esp.page_ranges) == 0)

        esp.reset()

        self.assertTrue(len(esp.bulk_search_data) == 0)
        self.assertTrue(len(esp.page_ranges) == 0)

    def test__esp_add_query_dict(self):
        esp = ESSearchProcessor()

        page = 1
        page_size = 20

        query = {
            "query": {
                "match": {
                    "_all": "foobar"
                }
            }
        }

        # ESSearchProcessor internally sets the from/size parameters
        # on the query; we need to compare with those values included
        query_with_size = query.copy()
        query_with_size.update({
            'from': (page - 1) * page_size,
            'size': page_size
        })

        esp.add_search(query.copy())
        ddtools.assert_equal(esp.bulk_search_data[0], {})
        ddtools.assert_equal(esp.bulk_search_data[1], query_with_size)

        esp.reset()
        esp.add_search(query.copy(), index='blog')
        ddtools.assert_equal(esp.bulk_search_data[0], {'index': 'blog'})
        ddtools.assert_equal(esp.bulk_search_data[1], query_with_size)

        esp.reset()
        esp.add_search(query.copy(), index='blog', doc_type='posts')
        ddtools.assert_equal(esp.bulk_search_data[0], {'index': 'blog', 'type': 'posts'})
        ddtools.assert_equal(esp.bulk_search_data[1], query_with_size)

    def test__esp_add_query_form(self):
        esp = ESSearchProcessor()

        page = 1
        page_size = 20

        query = self.form.prepare_query()

        # ESSearchProcessor internally sets the from/size parameters
        # on the query; we need to compare with those values included
        query_with_size = query.copy()
        query_with_size.update({
            'from': (page - 1) * page_size,
            'size': page_size
        })

        esp.add_search(self.form)
        ddtools.assert_equal(esp.bulk_search_data[0], {'index': 'blog', 'type': 'posts'})
        ddtools.assert_equal(esp.bulk_search_data[1], query_with_size)

    def test__esp_add_query_dslquery(self):
        page = 1
        page_size = 20

        query = {
            "query": {
                "match": {
                    "_all": "foobar"
                }
            }
        }

        s = Search.from_dict(query.copy())

        # ESSearchProcessor internally sets the from/size parameters
        # on the query; we need to compare with those values included
        query_with_size = query.copy()
        query_with_size.update({
            'from': (page - 1) * page_size,
            'size': page_size
        })

        esp = ESSearchProcessor()
        esp.add_search(s)
        ddtools.assert_equal(esp.bulk_search_data[0], {})
        ddtools.assert_equal(esp.bulk_search_data[1], query_with_size)

        esp.reset()
        esp.add_search(s, index='blog')
        ddtools.assert_equal(esp.bulk_search_data[0], {'index': 'blog'})
        ddtools.assert_equal(esp.bulk_search_data[1], query_with_size)

        esp.reset()
        esp.add_search(s, index='blog', doc_type='posts')
        ddtools.assert_equal(esp.bulk_search_data[0], {'index': 'blog', 'type': 'posts'})
        ddtools.assert_equal(esp.bulk_search_data[1], query_with_size)

        s = s.index('blog').params(routing='id')

        esp.reset()
        esp.add_search(s)
        ddtools.assert_equal(esp.bulk_search_data[0], {'index': ['blog'], 'routing': 'id'})
        ddtools.assert_equal(esp.bulk_search_data[1], query_with_size)

        s = s.doc_type('posts')

        esp.reset()
        esp.add_search(s)
        ddtools.assert_equal(esp.bulk_search_data[0], {'index': ['blog'], 'type': ['posts'], 'routing': 'id'})
        ddtools.assert_equal(esp.bulk_search_data[1], query_with_size)

    @mock.patch('simple_elasticsearch.forms.Elasticsearch.msearch')
    def test__esp_search(self, mock_msearch):
        mock_msearch.return_value = {
            "responses": [
                {
                    "hits": {
                        "hits": []
                    }
                }
            ]
        }

        esp = ESSearchProcessor()
        esp.add_search({}, index='blog', doc_type='posts')

        bulk_data = copy.deepcopy(esp.bulk_search_data)
        ddtools.assert_equal(bulk_data, [{'index': 'blog', 'type': 'posts'}, {'from': 0, 'size': 20}])

        responses = esp.search()
        mock_msearch.assert_called_with(bulk_data)

        # ensure that our hack to get size and from into the hit
        # data works
        self.assertTrue('size' in responses[0].get('hits'))
        self.assertTrue('from' in responses[0].get('hits'))

        # ensure that the bulk data gets reset
        self.assertEqual(len(esp.bulk_search_data), 0)
