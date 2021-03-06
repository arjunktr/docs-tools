import os
from multiprocessing import Pool, cpu_count

from fabric.api import lcd, local, task, env

from docs_meta import get_conf

env.FORCE = False
@task
def force():
    env.FORCE = True

env.PARALLEL = True
@task
def serial():
    env.PARALLEL = False

env.POOL = None
@task
def pool(value):
    env.POOL = int(value)

@task
def make(target):
    return _make(target)

def _make(target):
    with lcd(get_conf().build.paths.projectroot):
        if isinstance(target, list):
            target_str = make + ' '.join([target])
        elif isinstance(target, basestring):
            target_str = ' '.join(['make', target])

        local(target_str)

############### Dependency Checking ###############

def check_three_way_dependency(target, source, dependency):
    if not os.path.exists(target):
        # if .json doesn't exist, rebuild
        return True
    else:
        dep_mtime = os.stat(dependency).st_mtime
        if os.stat(source).st_mtime > dep_mtime:
            # if <file>.txt is older than <file>.fjson,
            return True
        elif dep_mtime > os.stat(target).st_mtime:
            #if fjson is older than json
            return True
        else:
            return False

def check_dependency(target, dependency):
    if dependency is None:
        return True

    if isinstance(target, list):
        return check_multi_dependency(target, dependency)

    if not os.path.exists(target):
        return True

    def needs_rebuild(targ_t, dep_f):
        if targ_t < os.stat(dep_f).st_mtime:
            return True
        else:
            return False

    target_time = os.stat(target).st_mtime
    if isinstance(dependency, list):
        ret = False
        for dep in dependency:
            if needs_rebuild(target_time, dep):
                ret = True
                break
        return ret
    else:
        return needs_rebuild(target_time, dependency)

def check_multi_dependency(target, dependency):
    for t in target:
        if check_dependency(t, dependency) is True:
            return True

    return False

############### Task Running Framework ###############

def runner(jobs, pool=None, retval='count'):
    if pool == 1 or env.PARALLEL is False:
        return sync_runner(jobs, env.FORCE, retval)
    else:
        if pool is None:
            pool = cpu_count()

        return async_runner(jobs, env.FORCE, pool, retval)

def async_runner(jobs, force, pool, retval):
    p = Pool(pool)

    count = 0
    results = []

    for job in jobs:
        if force is True or check_dependency(job['target'], job['dependency']):
           if isinstance(job['args'], dict):
                results.append(p.apply_async(job['job'], kwds=job['args']))
           else:
                results.append(p.apply_async(job['job'], args=job['args']))

           count += 1

    p.close()
    p.join()

    if retval == 'count':
        return count
    elif retval is None:
        return None
    elif retval == 'results':
        return [ o.get() for o in results ]
    else:
        return dict(count=count,
                    results=[ o.get() for o in results ])

def sync_runner(jobs, force, retval):
    count = 0
    results = []

    for job in jobs:
        if force is True or check_dependency(job['target'], job['dependency']):
            if isinstance(job['args'], dict):
                results.append(job['job'](**job['args']))
            else:
                results.append(job['job'](*job['args']))

            count +=1

    if retval == 'count':
        return count
    elif retval == 'results':
        return results
    elif retval is None:
        return None
    else:
        return dict(count=count,
                    results=results)
