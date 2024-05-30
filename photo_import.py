import os,sys,stat
import argparse
import filecmp
import re
from datetime import datetime
from PIL import Image
from PIL.ExifTags import TAGS

 # declare command line parameters
parser = argparse.ArgumentParser(description="Imports files from source directory to year-based subdirectories of destination",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument("src", help="Source directory")
parser.add_argument("dest", help="Destination directory")
args = parser.parse_args()

# get image creation time
def creation_time(entry):
    time=None
    # get time from exif
    try:
        stime=None
        stz=None
        # https://exiftool.org/TagNames/EXIF.html
        exif=Image.open(entry.path)._getexif()
        # 0x9003 	DateTimeOriginal 	string 	ExifIFD 	(date/time when original image was taken)   
        if 0x9003 in exif:
            stime=exif[0x9003]
            # 0x9011 	OffsetTimeOriginal 	string 	ExifIFD 	(time zone for DateTimeOriginal)
            if 0x9011 in exif:
                stz=exif[0x9011]
            # 0x882a 	TimeZoneOffset 	int16s[n] 	ExifIFD 	(1 or 2 values: 1. The time zone offset of DateTimeOriginal from GMT in hours, 2. If present, the time zone offset of ModifyDate)
            elif 0x882a in exif and len(exif[0x882a])>0:
                tmp=exif[0x882a][0]
                stz="{0}{1:0>2}:{2:0>2}".format('-' if tmp<0 else '+',abs(tmp)//60,abs(tmp)%60)
        # 0x9004 	CreateDate 	string 	ExifIFD 	(called DateTimeDigitized by the EXIF spec.)
        elif 0x9004 in exif:
            stime=exif[0x9004]
            # 0x9012 	OffsetTimeDigitized 	string 	ExifIFD 	(time zone for CreateDate)
            if 0x9012 in exif:
                stz=exif[0x9012]
        # 0x0132 	ModifyDate 	string 	IFD0 	(called DateTime by the EXIF spec.)
        elif 0x0132 in exif:
            stime=exif[0x0132];
            # 0x9010 	OffsetTime 	string 	ExifIFD 	(time zone for ModifyDate)
            if 0x9010 in exif:
                stz=exif[0x9010]
            # 0x882a 	TimeZoneOffset 	int16s[n] 	ExifIFD 	(1 or 2 values: 1. The time zone offset of DateTimeOriginal from GMT in hours, 2. If present, the time zone offset of ModifyDate)
            elif 0x882a in exif and len(exif[0x882a])>1:
                tmp=exif[0x882a][0]
                stz="{0}{1:0>2}:{2:0>2}".format('-' if tmp<0 else '+',abs(tmp)//60,abs(tmp)%60)
    except:
        pass
    # parse exif time
    if stime is not None:
        if stz is None:
            time=datetime.strptime(stime,'%Y:%m:%d %H:%M:%S')
        else:
            stz=stz.replace(':','')
            time=datetime.strptime(stime+stz,'%Y:%m:%d %H:%M:%S%z')
    # get time from filename
    if not time:
        matches=re.findall(r'_([0-9]{8}_[0-9]{6})', entry.name)
        if len(matches)>0:
            time=datetime.strptime(matches[0],'%Y%m%d_%H%M%S')
    # get time from file attributes
    if not time:
        stat=entry.stat()
        time=datetime.utcfromtimestamp(min(stat.st_atime,stat.st_mtime,stat.st_ctime))
    # return what we have
    return time

# moves file with checks
def move_file(srcpath,dstpath,allow_suffix=True):
    # the same path
    if srcpath==dstpath:
        return True
    # create directory
    os.makedirs(os.path.dirname(dstpath),exist_ok=True)
    # move file if it does not exist
    if not os.path.exists(dstpath):
        os.replace(srcpath,dstpath)
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
        for i in range(1,16):
            if move_file(srcpath,"{0}_copy{2}{1}".format(*(os.path.splitext(dstpath)),i),False):
                return True
        print('can not move '+srcpath+' to '+dstpath)
    # la problema
    return False

# moves file with supported extension to per-year subdirectories of dst
def process_file(entry,dst):
    if not entry.is_file():
        return
    # get destintaion path based on year creation
    time=creation_time(entry)
    dstpath=os.path.normpath(dst+'/'+str(time.year)+'/'+entry.name)
    # move file
    move_file(entry.path,dstpath)

# moves all files with supported extensions from src to per-year subdirectories of dst
def process_dir(src, dst, depth=16):
    # cut recursion
    if depth==0:
      return
    # process entries in directory
    with os.scandir(src) as entries:
        for entry in entries:
            if entry.is_dir():
                process_dir(entry.path,dst,depth-1);
            else:
                process_file(entry,dst)
# main flow
process_dir(os.path.abspath(args.src),os.path.abspath(args.dest))
