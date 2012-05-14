from django.http import HttpResponse
from django.shortcuts import render_to_response
from django.conf import settings
from django.template import RequestContext

from geonode.maps.views import default_map_config
from geonode.maps.models import *
from geonode.simplesearch.search import combined_search_results

# @hack - fix dependency by allowing injection
from mapstory.models import Section

import json

DEFAULT_MAPS_SEARCH_BATCH_SIZE = 10
MAX_MAPS_SEARCH_BATCH_SIZE = 25

def new_search_page(request, **kw):
    DEFAULT_MAP_CONFIG, DEFAULT_BASE_LAYERS = default_map_config(request)
    # for non-ajax requests, render a generic search page

    if request.method == 'GET':
        params = request.GET
    elif request.method == 'POST':
        params = request.POST
    else:
        return HttpResponse(status=405)
    
    if kw:
        params = dict(params)
        params.update(kw)

    map = Map(projection="EPSG:900913", zoom = 1, center_x = 0, center_y = 0)

    counts = {
        'maps' : Map.objects.count(),
        'layers' : Layer.objects.count(),
        'vector' : Layer.objects.filter(storeType='dataStore').count(),
        'raster' : Layer.objects.filter(storeType='coverageStore').count(),
        'users' : Contact.objects.count()
    }
    topics = Layer.objects.all().values_list('topic_category',flat=True)
    topic_cnts = {}
    for t in topics: topic_cnts[t] = topic_cnts.get(t,0) + 1
     
    return render_to_response('simplesearch/search.html', RequestContext(request, {
        'init_search': json.dumps(params or {}),
        'viewer_config': json.dumps(map.viewer_json(added_layers=DEFAULT_BASE_LAYERS, authenticated=request.user.is_authenticated())),
        'GOOGLE_API_KEY' : settings.GOOGLE_API_KEY,
        "site" : settings.SITEURL,
        'counts' : counts,
        'users' : User.objects.all(),
        'topics' : topic_cnts,
        'sections' : Section.objects.all(),
        'keywords' : get_all_keywords()
    }))
    
def get_all_keywords():
    if settings.USE_GEONETWORK:
        return Layer.objects.gn_catalog.get_all_keywords()
    allkw = {}
    for l in Layer.objects.exclude(keywords='').exclude(keywords__isnull=True).values_list('keywords',flat=True):
        kw = l.split()
        for k in kw:
            if k not in allkw:
                allkw[k] = 1
            else:
                allkw[k] += 1
    return allkw

def new_search_api(request):
    from time import time
    
    ts = time()
    params = _search_params(request)
    start = params[1]
    total, items = _new_search(*params)
    ts = time() - ts
    logger.info('generated combined search results in %s',ts)

    return _search_json(items, total, start, ts)

def new_search_api_reduced(request):
    from time import time

    ts = time()
    params = _search_params(request)
    total, items = _new_search(*params)
    ts = time() - ts
    logger.info('generated combined search results in %s',ts)
    idfun = lambda o: (isinstance(o, Map) and 'm%s' or 'l%s') % o.o.pk
    results = {
        "_time" : ts,
        "rows" : [ idfun(i) for i in items ],
        "total" : total
    }
    return HttpResponse(json.dumps(results), mimetype="application/json")

def _search_params(request):
    if request.method == 'GET':
        params = request.GET
    elif request.method == 'POST':
        params = request.POST
    else:
        return HttpResponse(status=405)

    # grab params directly to implement defaults as
    # opposed to panicy django forms behavior.
    query = params.get('q', '')
    try:
        start = int(params.get('start', '0'))
    except:
        start = 0
    try:
        limit = min(int(params.get('limit', DEFAULT_MAPS_SEARCH_BATCH_SIZE)),
                    MAX_MAPS_SEARCH_BATCH_SIZE)
    except:
        limit = DEFAULT_MAPS_SEARCH_BATCH_SIZE
        
    # handle old search link parameters
    if 'sort' in params and 'dir' in params:
        sort_field = params['sort']
        sort_asc = params['dir'] == 'ASC'
    else:    
        sort_field, sort_asc = {
            'newest' : ('last_modified',False),
            'oldest' : ('last_modified',True),
            'alphaaz' : ('title',True),
            'alphaza' : ('title',False),

        }[params.get('sort','newest')]

    filters = {}
    for k in ('bytype','bytopic','bykw','bysection','byowner','byextent'):
        if k in params:
            if params[k]:
                filters[k] = params[k]

    return query, start, limit, sort_field, sort_asc, filters
    
    
def _search_json(results, total, start, time):
    # unique item id for ext store (this could be done client side)
    iid = start
    for r in results:
        r.iid = iid
        iid += 1
        
    results = map(lambda r: r.as_dict(),results)
        
    results = {
        '_time' : time,
        'rows' : results,
        'total' :  total
    }
    results['success'] = True
    return HttpResponse(json.dumps(results), mimetype="application/json")

def _new_search(query, start, limit, sort_field, sort_asc, filters):

    results = combined_search_results(query, filters)

    filter_fun = []
    # careful when creating lambda or function filters inline like this
    # as multiple filters cannot use the same local variable or they
    # will overwrite each other
    
    # this is a cruddy, in-memory search since there is no database relationship
    if settings.USE_GEONETWORK:
        if 'bykw' in filters:
            kw = filters['bykw']
            filter_fun.append(lambda r: 'keywords' in r.as_dict() and kw in r.as_dict()['keywords'])
    
    for fun in filter_fun:
        results = filter(fun,results)

    # default sort order by id (could be last_modified when external layers are dealt with)
    results.sort(key=lambda r: getattr(r,sort_field),reverse=not sort_asc)

    return len(results), results[start:start+limit]

def author_list(req):
    q = User.objects.all()
    
    query = req.REQUEST.get('query',None)
    start = int(req.REQUEST.get('start',0))
    limit = int(req.REQUEST.get('limit',20))
    
    if query:
        q = q.filter(username__icontains=query)
        
    vals = q.values_list('username',flat=True)[start:start+limit]
    results = {
        'total' : q.count(),
        'names' : [ dict(name=v) for v in vals ]
    }
    return HttpResponse(json.dumps(results), mimetype="application/json")
    