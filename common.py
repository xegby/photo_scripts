import re
import os,sys,stat
import filecmp

# moves file with checks
def move_file(srcpath,dstpath,allow_suffix=True):
    # remove any '_copyNN' from dest
    if allow_suffix:
        dstpath=re.sub(r'_copy[0-9]*', '', dstpath)
    # the same path
    if srcpath==dstpath:
        return True
    # create directory
    os.makedirs(os.path.dirname(dstpath),exist_ok=True)
    # move file if it does not exist
    if not os.path.exists(dstpath):
        os.link(srcpath,dstpath)
        os.unlink(srcpath)
        return True
    # the same file, delete source
    if filecmp.cmp(srcpath,dstpath):
        os.remove(srcpath)
        return True
    # check if directory
    dststat=os.stat(dstpath)
    if stat.S_ISDIR(dststat.st_mode):
        if not allow_suffix:
            return False
    # files different, create copy
    if allow_suffix:
        for i in range(1,32):
            if move_file(srcpath,"{0}_copy{2}{1}".format(*(os.path.splitext(dstpath)),i),False):
                return True
        print('can not move '+srcpath+' to '+dstpath)
    # la problema
    return False

# compares two dictionaries ignoring sprcific keys
def compare_dict(a, b, ignore_keys={}):
    ka = set(a).difference(ignore_keys)
    kb = set(b).difference(ignore_keys)
    return ka == kb and all(a[k] == b[k] for k in ka)