try:
    from functools import wraps
except ImportError:
    from django.utils.functional import wraps  # Python 2.4 fallback.

from categories.models import Category
from mptt.templatetags.mptt_tags import cache_tree_children

#from django.db import IntegrityError
from django.core import exceptions
from django.core.urlresolvers import reverse
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.utils import simplejson
from django.utils.translation import ugettext as _

from askbot.conf import settings as askbot_settings
from askbot.models import Tag
from askbot.skins.loaders import render_into_skin
from askbot.utils.tokens import CategoriesApiTokenGenerator


def cats(request):
    """
    View that renders a simple page showing the categories tree widget.
    It uses JSON to send tree data in the context to the client.
    """
    if askbot_settings.ENABLE_CATEGORIES:
        return render_into_skin(
            'categories.html',
            {'cats_tree':simplejson.dumps(generate_tree())},
            request
        )
    else:
        raise Http404

def generate_tree():
    """
    Traverses a node tree and builds a structure easily serializable as JSON.
    """
    roots = cache_tree_children(Category.tree.all())
    if roots:
        # Assume we have one tree for now, this could change if we decide
        # against storing the root node in the DB
        return _recurse_tree(roots[0])
    return {}

def _recurse_tree(node):
    """
    Helper recursive function for generate_tree().
    Traverses recursively the node tree.
    """
    output = {'name': node.name, 'id': (node.tree_id, node.lft)}
    children = []
    if not node.is_leaf_node():
        for child in node.get_children():
            children.append(_recurse_tree(child))
    output['children'] = children
    return output

def admin_ajax_post(view_func):
    """
    Decorator for Django views that checks that the request is:
    * Sent via ajax
    * Uses POST
    * by an authenticated Askbot administrator user
    """
    @wraps(view_func)
    def inner(request, *args, **kwargs):
        if not askbot_settings.ENABLE_CATEGORIES:
            raise Http404
        try:
            if not request.is_ajax():
                #todo: show error page but no-one is likely to get here
                return HttpResponseRedirect(reverse('index'))
            if request.method != 'POST':
                raise exceptions.PermissionDenied('must use POST request')
            if not request.user.is_authenticated():
                raise exceptions.PermissionDenied(
                    _('Sorry, but anonymous users cannot access this view')
                )
            if not request.user.is_administrator():
                raise exceptions.PermissionDenied(
                    _('Sorry, but you cannot access this view')
                )
            return view_func(request, *args, **kwargs)
        except Exception, e:
            response_data = dict()
            message = unicode(e)
            if message == '':
                message = _('Oops, apologies - there was some error')
            response_data['message'] = message
            response_data['success'] = False
            response_data['result'] = 'error'
            data = simplejson.dumps(response_data)
            return HttpResponse(data, mimetype="application/json")
    return inner

@admin_ajax_post
def add_category(request):
    """
    Adds a category. Meant to be called by the site administrator using ajax
    and POST HTTP method.
    The expected json request is an object with the following keys:
      'name':   Name of the new category to be created.
      'parent': ID of the parent category for the category to be created.
    The response is also a json object with keys:
      'success': boolean
      'message': text description in case of failure (not always present)

    Category IDs are two-elements [tree_id, left id] JS arrays (Python tuples)
    """
    post_data = simplejson.loads(request.raw_post_data)
    parent = post_data.get('parent')
    new_name = post_data.get('name')
    if not new_name:
        raise exceptions.ValidationError(
            _("Missing or invalid new category name parameter")
            )
    # TODO: there is a chance of a race condition here
    if parent:
        try:
            parent = Category.objects.get(
                    tree_id=parent[0],
                    lft=parent[1]
                )
        except Category.DoesNotExist:
            raise exceptions.ValidationError(
                _("Requested parent category doesn't exist")
                )
    cat, created = Category.objects.get_or_create(name=new_name, defaults={'parent': parent})
    if not created:
        raise exceptions.ValidationError(
            _('There is already a category with that name')
            )
    response_data = {'success': True}
    response_data['result'] = 'success'
    data = simplejson.dumps(response_data)
    return HttpResponse(data, mimetype="application/json")

@admin_ajax_post
def rename_category(request):
    """
    Change the name of a category. Meant to be called by the site administrator
    using ajax and POST HTTP method.
    The expected json request is an object with the following keys:
      'id':   ID of the category to be renamed.
      'name': New name of the category.
    The response is also a json object with keys:
      'success': boolean
      'message': text description in case of failure (not always present)

    Category IDs are two-elements [tree_id, left id] JS arrays (Python tuples)
    """
    post_data = simplejson.loads(request.raw_post_data)
    new_name = post_data.get('name')
    cat_id = post_data.get('id')
    if not new_name or not cat_id:
        raise exceptions.ValidationError(
            _("Missing or invalid required parameter")
            )
    response_data = dict()
    # TODO: there is a chance of a race condition here
    try:
        node = Category.objects.get(
                tree_id=cat_id[0],
                lft=cat_id[1]
            )
    except Category.DoesNotExist:
        raise exceptions.ValidationError(
            _("Requested category doesn't exist")
            )
    if new_name != node.name:
        try:
            node = Category.objects.get(name=new_name)
        except Category.DoesNotExist:
            pass
        else:
            raise exceptions.ValidationError(
                _('There is already a category with that name')
                )
        node.name=new_name
        # Let any exception that happens during save bubble up, for now
        node.save()
        response_data['success'] = True
        response_data['result'] = 'success'
    else:
        response_data = {'result': 'noop'}
    data = simplejson.dumps(response_data)
    return HttpResponse(data, mimetype="application/json")

@admin_ajax_post
def add_tag_to_category(request):
    """
    Adds a tag to a category. Meant to be called by the site administrator using ajax
    and POST HTTP method.
    Both the tag and the category must exist and their IDs are provided to
    the view.
    The expected json request is an object with the following keys:
      'tag_id': ID of the tag.
      'cat_id': ID of the category.
    The response is also a json object with keys:
      'success': boolean
      'message': text description in case of failure (not always present)

    Category IDs are two-elements [tree_id, left id] JS arrays (Python tuples)
    """
    post_data = simplejson.loads(request.raw_post_data)
    tag_id = post_data.get('tag_id')
    cat_id = post_data.get('cat_id')
    if not tag_id or cat_id is None:
        raise exceptions.ValidationError(
            _("Missing required parameter")
            )
    # TODO: there is a chance of a race condition here
    try:
        cat = Category.objects.get(
                tree_id=cat_id[0],
                lft=cat_id[1]
            )
    except Category.DoesNotExist:
        raise exceptions.ValidationError(
            _("Requested category doesn't exist")
            )
    try:
        tag = Tag.objects.get(id=tag_id)
    except Tag.DoesNotExist:
        raise exceptions.ValidationError(
            _("Requested tag doesn't exist")
            )
    # Let any exception that could happen during save bubble up
    tag.categories.add(cat)
    response_data = { 'success': True }
    response_data['result'] = 'success'
    data = simplejson.dumps(response_data)
    return HttpResponse(data, mimetype="application/json")

def get_tag_categories(request):
    """
    Get the categories a tag belongs to. Meant to be called using ajax
    and POST HTTP method. Available to everyone including anonymous users.
    The expected json request is an object with the following key:
      'tag_id': ID of the tag. (required)
    The response is also a json object with keys:
      'success': boolean
      'cats':    a list of two-elements lists containing category ID
         (integer) name (string) for each category
      'message': text description in case of failure (not always present)
    """
    if not askbot_settings.ENABLE_CATEGORIES:
        raise Http404
    response_data = dict()
    try:
        if request.is_ajax():
            if request.method == 'POST':
                post_data = simplejson.loads(request.raw_post_data)
                tag_id = post_data.get('tag_id')
                if not tag_id:
                    raise exceptions.ValidationError(
                        _("Missing tag_id parameter")
                        )
                try:
                    tag = Tag.objects.get(id=tag_id)
                except Tag.DoesNotExist:
                    raise exceptions.ValidationError(
                        _("Requested tag doesn't exist")
                        )
                response_data['cats'] = list(tag.categories.values('id', 'name'))
                response_data['success'] = True
                response_data['result'] = 'success'
                data = simplejson.dumps(response_data)
                return HttpResponse(data, mimetype="application/json")
            else:
                raise exceptions.PermissionDenied('must use POST request')
        else:
            #todo: show error page but no-one is likely to get here
            return HttpResponseRedirect(reverse('index'))
    except Exception, e:
        message = unicode(e)
        if message == '':
            message = _('Oops, apologies - there was some error')
        response_data['message'] = message
        response_data['success'] = False
        response_data['result'] = 'success'
        data = simplejson.dumps(response_data)
        return HttpResponse(data, mimetype="application/json")

def remove_tag_from_category(request):
    """
    Remove a tag from a category it tag belongs to. Meant to be called using ajax
    and POST HTTP method. Available to admin and moderators users.
    The expected json request is an object with the following keys:
      'tag_id': ID of the tag.
      'cat_id': ID of the category.
    The response is also a json object with keys:
      'success': boolean
      'cats':    a list of two-elements lists containing category ID
         (integer) name (string) for each category
      'message': text description in case of failure (not always present)

    Category IDs are two-elements [tree_id, left id] JS arrays (Python tuples)
    """
    if not askbot_settings.ENABLE_CATEGORIES:
        raise Http404
    response_data = dict()
    try:
        if request.is_ajax():
            if request.method == 'POST':
                if request.user.is_authenticated():
                    if request.user.is_administrator() or request.user.is_moderator():
                        post_data = simplejson.loads(request.raw_post_data)
                        tag_id = post_data.get('tag_id')
                        cat_id = post_data.get('cat_id')
                        if not tag_id or cat_id is None:
                            raise exceptions.ValidationError(
                                _("Missing required parameter")
                                )
                        # TODO: there is a chance of a race condition here
                        try:
                            cat = Category.objects.get(
                                    tree_id=cat_id[0],
                                    lft=cat_id[1]
                                )
                        except Category.DoesNotExist:
                            raise exceptions.ValidationError(
                                _("Requested category doesn't exist")
                                )
                        try:
                            tag = Tag.objects.get(id=tag_id)
                        except Tag.DoesNotExist:
                            raise exceptions.ValidationError(
                                _("Requested tag doesn't exist")
                                )
                        if cat.tags.filter(id=tag.id).count():
                            # Let any exception that happens during save bubble up
                            cat.tags.remove(tag)
                            response_data['success'] = True
                            response_data['result'] = 'error'
                        else:
                            response_data['result'] = 'noop'
                        data = simplejson.dumps(response_data)
                        return HttpResponse(data, mimetype="application/json")
                    else:
                        raise exceptions.PermissionDenied(
                            _('Sorry, but you cannot access this view')
                        )
                else:
                    raise exceptions.PermissionDenied(
                        _('Sorry, but anonymous users cannot access this view')
                    )
            else:
                raise exceptions.PermissionDenied('must use POST request')
        else:
            #todo: show error page but no-one is likely to get here
            return HttpResponseRedirect(reverse('index'))
    except Exception, e:
        message = unicode(e)
        if message == '':
            message = _('Oops, apologies - there was some error')
        response_data['message'] = message
        response_data['success'] = False
        response_data['result'] = 'error'
        data = simplejson.dumps(response_data)
        return HttpResponse(data, mimetype="application/json")

@admin_ajax_post
def delete_category(request):
    """
    Remove a category. Meant to be called by the site administrator using ajax
    and POST HTTP method.
    The expected json request is an object with the following key:
      'id':   ID of the category to be renamed.
      'token': A category deletion token obtained form a previous call (optional)
    The response is also a json object with keys:
      'success': boolean
      'message': text description in case of failure (not always present)
      'token': A category deletion token that should be used to confirm the
               operation (not always present)

    Category IDs are two-elements [tree_id, left id] JS arrays (Python tuples)

    When a category that is associated with one or more tags the view returns
    a 'status' of 'need_confirmation' and provides a 'tags' response key with
    a list of such tags and 'token' response key whose value can ve used in a
    new call to delete the same category object.

    Tokens are opaque strings with a maximum length of 20 and with a validity
    lifetime of ten minutes.
    """
    response_data = dict()
    post_data = simplejson.loads(request.raw_post_data)
    cat_id = post_data.get('id')
    if not cat_id:
        raise exceptions.ValidationError(
            _("Missing or invalid required parameter")
            )
    try:
        node = Category.objects.get(
                tree_id=cat_id[0],
                lft=cat_id[1]
            )
    except Category.DoesNotExist:
        raise exceptions.ValidationError(
            _("Requested category doesn't exist")
            )
    token = post_data.get('token')
    if token is not None:
        # verify token + Category instance combination
        if not CategoriesApiTokenGenerator().check_token(node, token):
            raise exceptions.ValidationError(
                _("Invalid token provided")
                )

    tag_count = node.tags.count()
    has_children = not node.is_leaf_node()
    if not tag_count and not has_children:
        # Let any exception that happens during deletion bubble up
        node.delete()
        response_data['success'] = True
        response_data['result'] = 'success'
    elif has_children:
        response_data['success'] = False
        response_data['result'] = 'cannot_delete_subcategories'
    elif tag_count:
        if token is None:
            response_data['result'] = 'need_confirmation'
            response_data['token'] = CategoriesApiTokenGenerator().make_token(node)
            response_data['tags'] = [t[0] for t in node.tags.values_list('name')]
        else:
            # Let any exception that happens during deletion bubble up
            node.tags.clear()
            node.delete()
            response_data['success'] = True
            response_data['result'] = 'success'
    data = simplejson.dumps(response_data)
    return HttpResponse(data, mimetype="application/json")