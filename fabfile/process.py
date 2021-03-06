import collections
import json
import os
import re
import shutil
import subprocess

from fabric.api import task, env, abort, puts, local

from docs_meta import output_yaml, get_manual_path, get_conf
from make import check_dependency, check_three_way_dependency, runner
from utils import md5_file, symlink, expand_tree, dot_concat, ingest_yaml_list

env.input_file = None
env.output_file = None

@task
def input(fn):
    env.input_file = fn

@task
def output(fn):
    env.output_file = fn

########## Process Sphinx Json Output ##########

def json_output(conf=None):
    if env.input_file is None or env.output_file is None:
        all_json_output(conf)
    else:
        process_json_file(env.input_file, env.output_file)

def all_json_output(conf=None):
    if conf is None:
        conf = get_conf()

    count = runner(json_output_jobs(conf))

    puts('[json]: processed {0} json files.'.format(str(count)))

    list_file = os.path.join(conf.build.paths.branch_staging, 'json-file-list')
    public_list_file = os.path.join(conf.build.paths.public_site_output,
                                    'json', '.file_list')

    cmd = 'rsync --recursive --times --delete --exclude="*pickle" --exclude=".buildinfo" --exclude="*fjson" {src} {dst}'
    json_dst = os.path.join(conf.build.paths.public_site_output, 'json')

    if not os.path.exists(json_dst):
        os.makedirs(json_dst)

    local(cmd.format(src=os.path.join(conf.build.paths.branch_output, 'json') + '/',
                     dst=json_dst))

    copy_if_needed(list_file, public_list_file)
    puts('[json]: deployed json files to local staging.')

def json_output_jobs(conf=None):
    if conf is None:
        conf = get_conf()

    regexes = [
        (re.compile(r'<a class=\"headerlink\"'), '<a'),
        (re.compile(r'<[^>]*>'), ''),
        (re.compile(r'&#8220;'), '"'),
        (re.compile(r'&#8221;'), '"'),
        (re.compile(r'&#8216;'), "'"),
        (re.compile(r'&#8217;'), "'"),
        (re.compile(r'&#\d{4};'), ''),
        (re.compile(r'&nbsp;'), ''),
        (re.compile(r'&gt;'), '>'),
        (re.compile(r'&lt;'), '<')
    ]

    outputs = []
    for fn in expand_tree('source', 'txt'):
        # path = build/<branch>/json/<filename>
        if conf.project.name == 'mms':
            path = os.path.join(conf.build.paths.branch_staging,
                                'json', os.path.splitext(fn.split(os.path.sep, 1)[1])[0])
        else:
            path = os.path.join(conf.build.paths.branch_output,
                                'json', os.path.splitext(fn.split(os.path.sep, 1)[1])[0])
        fjson = dot_concat(path, 'fjson')
        json = dot_concat(path, 'json')

        yield dict(target=json,
                   dependency=fjson,
                   job=process_json_file,
                   args=(fjson, json, regexes, conf))
        outputs.append(json)

    list_file = os.path.join(conf.build.paths.branch_staging, 'json-file-list')

    yield dict(target=list_file,
               dependency=None,
               job=generate_list_file,
               args=(outputs, list_file, conf))

def process_json_file(input_fn, output_fn, regexes, conf=None):
    with open(input_fn, 'r') as f:
        document = f.read()

    doc = json.loads(document)

    if 'body' in doc:
        text = doc['body'].encode('ascii', 'ignore')
        text = munge_content(text, regexes)

        doc['text'] = ' '.join(text.split('\n')).strip()

    if 'title' in doc:
        title = doc['title'].encode('ascii', 'ignore')
        title = munge_content(title, regexes)

        doc['title'] = title

    if conf.project.name == 'mms':
        if conf.project.edition == 'hosted':
            url = ['http://mms.mongodb.com/help-hosted', get_manaul_path() ]
        else:
            url = ['http://mms.mongodb.com/help' ]
    else:
        url = [ 'http://docs.mongodb.org', get_manual_path() ]

    url.extend(input_fn.rsplit('.', 1)[0].split(os.path.sep)[3:])
    doc['url'] = '/'.join(url) + '/'

    with open(output_fn, 'w') as f:
        f.write(json.dumps(doc))

    puts('[json]: generated a processed json file: ' + output_fn)

def generate_list_file(outputs, path, conf=None):
    dirname = os.path.dirname(path)

    if conf is None:
        conf = get_conf()

    if conf.project.name == 'ecosystem':
        url = 'http://docs.mongodb.org/ecosystem'
    elif conf.project.name == 'mms':
        if conf.project.edition == 'hosted':
            url = '/'.join(['http://mms.mongodb.com/help-hosted', get_manual_path()])
        else:
            url = 'http://mms.mongodb.com/help'
    else:
        url = '/'.join(['http://docs.mongodb.org', get_manual_path()])

    if not os.path.exists(dirname):
        os.mkdir(dirname)

    with open(path, 'w') as f:
        for fn in outputs:
            f.write( '/'.join([ url, 'json', fn.split('/', 3)[3:][0]]) )
            f.write('\n')

    puts('[json]: rebuilt inventory of json output.')

########## Update Dependencies ##########

def update_dependency(fn):
    if os.path.exists(fn):
        os.utime(fn, None)
        puts('[dependency]: updated timestamp of {0} because its included files changed'.format(fn))

def fix_include_path(inc, fn, source):
    if inc.startswith('/'):
        return ''.join([source + inc])
    else:
        return os.path.join(os.path.dirname(os.path.abspath(fn)), fn)

def check_deps(file, pattern):
    includes = []
    try:
        with open(file, 'r') as f:
            for line in f:
                r = pattern.findall(line)
                if r:
                    includes.append(fix_include_path(r[0], file, 'source'))
        if len(includes) >= 1:
            if check_dependency(file, includes):
                update_dependency(file)
    except IOError:
        pass

def refresh_dependencies():
    count = runner(composite_jobs())
    puts('[dependency]: updated timestamps of {0} files'.format(count))

def composite_jobs():
    files = expand_tree('source', 'txt')
    inc_pattern = re.compile(r'\.\. include:: (.*\.(?:txt|rst))')

    for fn in files:
        yield {
                'target': fn,
                'dependency': None,
                'job': check_deps,
                'args': [ fn, inc_pattern ]
              }

########## Simple Tasks ##########

@task
def meta():
    output_yaml(env.output_file)

@task
def touch(fn, times=None):
    if os.path.exists(fn):
        os.utime(fn, times)

########## Main Output Processing Targets ##########

class InvalidPath(Exception): pass

def copy_always(source_file, target_file, name='build'):
    if os.path.isfile(source_file) is False:
        puts("[{0}]: Input file '{1}' does not exist.".format(name, source_file))
        raise InvalidPath
    else:
        if not os.path.exists(os.path.dirname(target_file)):
            os.makedirs(os.path.dirname(target_file))
        shutil.copyfile(source_file, target_file)

    puts('[{0}]: copied {1} to {2}'.format(name, source_file, target_file))

def copy_if_needed(source_file, target_file, name='build'):
    if os.path.isfile(source_file) is False or os.path.isdir(source_file):
        puts("[{0}]: Input file '{1}' does not exist.".format(name, source_file))
        raise InvalidPath
    elif os.path.isfile(target_file) is False:
        if not os.path.exists(os.path.dirname(target_file)):
            os.makedirs(os.path.dirname(target_file))
        shutil.copyfile(source_file, target_file)

        if name is not None:
            puts('[{0}]: created "{1}" which did not exist.'.format(name, source_file))
    else:
        if md5_file(source_file) == md5_file(target_file):
            if name is not None:
                puts('[{0}]: "{1}" not changed.'.format(name, source_file))
        else:
            shutil.copyfile(source_file, target_file)

            if name is not None:
                puts('[{0}]: "{1}" changed. Updated: {2}'.format(name, source_file, target_file))

@task
def create_link():
    _create_link(env.input_file, env.output_file)

def _create_link(input_fn, output_fn):
    out_dirname = os.path.dirname(output_fn)
    if out_dirname != '' and not os.path.exists(out_dirname):
        os.makedirs(out_dirname)

    if os.path.islink(output_fn):
        os.remove(output_fn)
    elif os.path.isdir(output_fn):
        abort('[{0}]: {1} exists and is a directory'.format('link', output_fn))
    elif os.path.exists(output_fn):
        abort('[{0}]: could not create a symlink at {1}.'.format('link', output_fn))

    out_base = os.path.basename(output_fn)
    if out_base == "":
       abort('[{0}]: could not create a symlink at {1}.'.format('link', output_fn))
    else:
        symlink(out_base, input_fn)
        os.rename(out_base, output_fn)
        puts('[{0}] created symbolic link pointing to "{1}" named "{2}"'.format('symlink', input_fn, out_base))

def manual_single_html(input_file, output_file):
    # don't rebuild this if its not needed.
    if check_dependency(output_file, input_file) is True:
        pass
    else:
        puts('[process] [single]: singlehtml not changed, not reprocessing.')
        return False

    with open(input_file, 'r') as f:
        text = f.read()

    text = re.sub('href="contents.html', 'href="index.html', text)
    text = re.sub('name="robots" content="index"', 'name="robots" content="noindex"', text)
    text = re.sub('(href=")genindex.html', '\1../genindex/', text)

    with open(output_file, 'w') as f:
        f.write(text)

    puts('[process] [single]: processed singlehtml file.')

#################### PDFs from Latex Produced by Sphinx  ####################

def _clean_sphinx_latex(fn, regexes):
    with open(fn, 'r') as f:
        tex = f.read()

    for regex, subst in regexes:
        tex = regex.sub(subst, tex)

    with open(fn, 'w') as f:
        f.write(tex)

    puts('[pdf]: processed Sphinx latex format for {0}'.format(fn))

def _render_tex_into_pdf(fn, path):
    pdflatex = 'TEXINPUTS=".:{0}:" pdflatex --interaction batchmode --output-directory {0} {1}'.format(path, fn)

    try:
        with open(os.devnull, 'w') as f:
            subprocess.check_call(pdflatex, shell=True, stdout=f, stderr=f)
    except subprocess.CalledProcessError:
        print('[ERROR]: {0} file has errors, regenerate and try again'.format(fn))
        return False

    puts('[pdf]: completed pdf rendering stage 1 of 4 for: {0}'.format(fn))

    try:
        with open(os.devnull, 'w') as f:
            subprocess.check_call("makeindex -s {0}/python.ist {0}/{1}.idx ".format(path, os.path.basename(fn)[:-4]), shell=True, stdout=f, stderr=f)
    except subprocess.CalledProcessError:
        print('[ERROR]: {0} file has errors, regenerate and try again'.format(fn))
    puts('[pdf]: completed pdf rendering stage 2 of 4 (indexing) for: {0}'.format(fn))

    try:
        with open(os.devnull, 'w') as f:
            subprocess.check_call(pdflatex, shell=True, stdout=f, stderr=f)
    except subprocess.CalledProcessError:
        print('[ERROR]: {0} file has errors, regenerate and try again'.format(fn))
        return False
    puts('[pdf]: completed pdf rendering stage 3 of 4 for: {0}'.format(fn))

    try:
        with open(os.devnull, 'w') as f:
            subprocess.check_call(pdflatex, shell=True, stdout=f, stderr=f)
    except subprocess.CalledProcessError:
        print('[ERROR]: {0} file has errors, regenerate and try again'.format(fn))
        return False
    puts('[pdf]: completed pdf rendering stage 4 of 4 for: {0}'.format(fn))

    puts('[pdf]: rendered {0}.{1}'.format(os.path.basename(fn), 'pdf'))

@task
def pdfs():
    it = 0
    for queue in pdf_jobs():
        it += 1
        count = runner(queue)
        puts("[pdf]: completed {0} pdf jobs, in stage {1}".format(count, it))

def pdf_jobs():
    conf = get_conf()

    pdfs = ingest_yaml_list(os.path.join(conf.build.paths.builddata, 'pdfs.yaml'))
    tex_regexes = [ ( re.compile(r'(index|bfcode)\{(.*)--(.*)\}'),
                      r'\1\{\2-\{-\}\3\}'),
                    ( re.compile(r'\\PYGZsq{}'), "'"),
                    ( re.compile(r'\\code\{/(?!.*{}/|etc|usr|data|var|srv)'),
                      r'\code{' + conf.project.url + r'/' + conf.project.tag) ]

    # this is temporary
    queue = ( [], [], [], [], [] )

    for i in pdfs:
        tagged_name = i['output'][:-4] + '-' + i['tag']
        deploy_fn = tagged_name + '-' + conf.git.branches.current + '.pdf'
        link_name = deploy_fn.replace('-' + conf.git.branches.current, '')

        if 'edition' in i:
            deploy_path = os.path.join(conf.build.paths.public, i['edition'])
            if i['edition'] == 'hosted':
                deploy_path = os.path.join(deploy_path,  conf.git.branches.current)
                latex_dir = os.path.join(conf.build.paths.output, i['edition'],
                                         conf.git.branches.current, 'latex')
            else:
                latex_dir = os.path.join(conf.build.paths.output, i['edition'], 'latex')
                deploy_fn = tagged_name + '.pdf'
                link_name = deploy_fn
        else:
            deploy_path = conf.build.paths['branch-staging']
            latex_dir = os.path.join(conf.build.paths['branch-output'], 'latex')

        i['source'] = os.path.join(latex_dir, i['output'])
        i['processed'] = os.path.join(latex_dir, tagged_name + '.tex')
        i['pdf'] = os.path.join(latex_dir, tagged_name + '.pdf')
        i['deployed'] = os.path.join(deploy_path, deploy_fn)
        i['link'] = os.path.join(deploy_path, link_name)
        i['path'] = latex_dir

        # these appends will become yields, once runner() can be dependency
        # aware.
        queue[0].append(dict(dependency=None,
                             target=i['source'],
                             job=_clean_sphinx_latex,
                             args=(i['source'], tex_regexes)))

        queue[1].append(dict(dependency=i['source'],
                             target=i['processed'],
                             job=copy_if_needed,
                             args=(i['source'], i['processed'], 'pdf')))

        queue[2].append(dict(dependency=i['processed'],
                             target=i['pdf'],
                             job=_render_tex_into_pdf,
                             args=(i['processed'], i['path'])))

        queue[3].append(dict(dependency=i['pdf'],
                             target=i['deployed'],
                             job=copy_if_needed,
                             args=(i['pdf'], i['deployed'], 'pdf')))

        if i['link'] != i['deployed']:
            queue[4].append(dict(dependency=i['deployed'],
                                 target=i['link'],
                                 job=_create_link,
                                 args=(deploy_fn, i['link'])))

    return queue

#################### Error Page Processing ####################

# this is called directly from the sphinx generation function in sphinx.py.

def munge_page(fn, regex, out_fn=None,  tag='build'):
    with open(fn, 'r') as f:
        page = f.read()

    page = munge_content(page, regex)

    if out_fn is None:
        out_fn = fn

    with open(out_fn, 'w') as f:
        f.write(page)

    puts('[{0}]: processed {1}'.format(tag, fn))

def munge_content(content, regex):
    if isinstance(regex, list):
        for cregex, subst in regex:
            content = cregex.sub(subst, content)
        return content
    else:
        return regex[0].sub(regex[1], content)

def error_pages():
    conf = get_conf()

    error_conf = os.path.join(conf.build.paths.builddata, 'errors.yaml')

    if not os.path.exists(error_conf):
        return None
    else:
        error_pages = ingest_yaml_list(error_conf)

        sub = (re.compile(r'\.\./\.\./'), conf.project.url + r'/' + conf.project.tag + r'/')

        for error in error_pages:
            page = os.path.join(conf.build.paths.projectroot,
                                conf.build.paths['branch-output'], 'dirhtml',
                                'meta', error, 'index.html')
            munge_page(fn=page, regex=sub, tag='error-pages')

        puts('[error-pages]: rendered {0} error pages'.format(len(error_pages)))

#################### Manpage Processing ####################

def manpage_url(regex_obj, input_file=None):
    if input_file is None:
        if env.input_file is None:
            abort('[man]: you must specify input and output files.')
        else:
            input_file = env.input_file

    with open(input_file, 'r') as f:
        manpage = f.read()

    if isinstance(regex_obj, list):
        for regex, subst in regex_obj:
            manpage = regex.sub(subst, manpage)
    else:
        manpage = regex_obj[0].sub(regex_obj[1], manpage)

    with open(input_file, 'w') as f:
        f.write(manpage)

    puts("[{0}]: fixed urls in {1}".format('man', input_file))

def manpage_url_jobs():
    conf = get_conf()

    project_source = os.path.join(conf.build.paths.projectroot,
                                  conf.build.paths.source)

    top_level_items = set()
    for fs_obj in os.listdir(project_source):
        if fs_obj.startswith('.static') or fs_obj == 'index.txt':
            continue
        if os.path.isdir(os.path.join(project_source, fs_obj)):
            top_level_items.add(fs_obj)
        if fs_obj.endswith('.txt'):
            top_level_items.add(fs_obj[:-4])

    top_level_items = '/'+ r'[^\s]*|/'.join(top_level_items) + r'[^\s]*'

    re_string = r'(\\fB({0})\\fP)'.format(top_level_items).replace(r'-', r'\-')
    subst = conf.project.url + '/' + conf.project.tag + r'\2'

    regex_obj = (re.compile(re_string), subst)

    for manpage in expand_tree(os.path.join(conf.build.paths.projectroot,
                                            conf.build.paths.output,
                                            conf.git.branches.current,
                                            'man'), ['1', '5']):
        yield dict(target=manpage,
                   dependency=None,
                   job=manpage_url,
                   args=[regex_obj, manpage])


def _process_page(fn, output_fn, regex, builder='processor'):
    tmp_fn = fn + '~'

    jobs = [
             {
               'target': tmp_fn,
               'dependency': fn,
               'job': munge_page,
               'args': dict(fn=fn, out_fn=tmp_fn, regex=regex),
             },
             {
               'target': output_fn,
               'dependency': tmp_fn,
               'job': copy_always,
               'args': dict(source_file=tmp_fn,
                            target_file=output_fn,
                            name=builder),
             }
           ]

    runner(jobs, pool=1)

def manpage_jobs():
    conf = get_conf()

    jobs = [
        (
            os.path.join(conf.build.paths.includes, "manpage-options-auth.rst"),
            os.path.join(conf.build.paths.includes, 'manpage-options-auth-mongo.rst'),
            ( re.compile('fact-authentication-source-tool'),
              'fact-authentication-source-mongo' )
        ),
        (
            os.path.join(conf.build.paths.includes, 'manpage-options-ssl.rst'),
            os.path.join(conf.build.paths.includes, 'manpage-options-ssl-settings.rst'),
            [ (re.compile(r'\.\. option:: --'), r'.. setting:: ' ),
              (re.compile(r'setting:: (\w+) .*'), r'setting:: \1'),
              (re.compile(r':option:`--'), r':setting:`') ]
        )
    ]

    for input_fn, output_fn, regex in jobs:
        yield {
                'target': output_fn,
                'dependency': output_fn,
                'job': _process_page,
                'args': [ input_fn, output_fn, regex, 'manpage' ],
              }

def post_process_jobs(source_fn=None, tasks=None, conf=None):
    if tasks is None:
        if conf is None:
            conf = get_conf()

        if source_fn is None:
            source_fn = os.path.join(conf.build.paths.project.root,
                                     conf.build.paths.builddata,
                                     'processing.yaml')
        tasks = ingest_yaml(source_fn)
    elif not isinstance(tasks, collections.Iterable):
        abort('[ERROR]: cannot parse post processing specification.')

    def rjob(fn, regex, type):
        return {
                 'target': fn,
                 'dependency': None,
                 'job': _process_page,
                 'args': dict(fn=fn, output_fn=fn, regex=regex, builder=type)
               }

    for job in tasks:
        if not isinstance(job, dict):
            abort('[ERROR]: invalid replacement specification.')
        elif not 'file' in job and not 'transform' in job:
            abort('[ERROR]: replacement specification incomplete.')

        if 'type' not in job:
            job['type'] = 'processor'

        if isinstance(job['transform'], list):
            regex = [ ( re.compile(rs['regex'], rs['replace'] ) ) for rs  in job['transform'] ]
        else:
            regex = ( re.compile(job['transform']['regex'] ), job['transform']['replace'])

        if isinstance(job['file'], list):
            for fn in job['file']:
                yield rjob(fn, regex, job['type'])
        else:
            yield rjob(fn, regex, job['type'])
