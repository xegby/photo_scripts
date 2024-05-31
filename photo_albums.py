import os,sys,stat
import argparse
import json
import time
from pathlib import Path
from httplib2 import Http
from datetime import datetime,timezone
from oauth2client import file as oa2file
from oauth2client import client as oa2client
from oauth2client import tools as oa2tools
import common

SCOPES = 'https://www.googleapis.com/auth/photoslibrary.readonly'

# declare command line parameters
parser = argparse.ArgumentParser(description="Downloads Google Photos albums trying to prevent downloading known existing files",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument("--trashbin", default=None, help="Directory to store photos which are no longer in album")
parser.add_argument("--key_file", default="key.json", help="Application keys file location")
parser.add_argument("--creds_file", default="credentials.json", help="User credentials file location")
parser.add_argument('--skip_new', action='store_true', help="Skip new Google Photos albums downloading")
parser.add_argument("destination", help="Destination download directory")
args = parser.parse_args()

# prepare authorization request
def Authorize(keys,credentials):
    store = oa2file.Storage(credentials)
    creds = store.get()
    if not creds or creds.invalid:
        flow = oa2client.flow_from_clientsecrets(filename=keys, scope=SCOPES)
        creds = oa2tools.run_flow(flow, storage=store, flags=oa2tools.argparser.parse_args([]))
    return(creds.authorize(Http()))

# load existing albums list
def LoadAlbums(dest):
    albums={}
    for path in dest.rglob('album.json'):
        with path.open() as file:
            data=json.load(file);
            items={}
            data['path']=str(path.absolute().parent)
            if 'downloadTime' not in data:
                data['downloadTime']=0
            albums[data['id']]=data
    return albums

# load ignore list
def LoadIgnore(dest):
    ignore=[]
    for path in dest.rglob('ignore.*'):
        with path.open() as file:
            ignore+=file.read().splitlines()
    return ignore

# dowload all albums
def DowloadAlbums(req,trash,dest,old,ignore,skip_new):
    nextpage='0'
    new={}
    while nextpage is not None:
        # get album data
        if nextpage=='0':
            (resp, content) = req.request("https://photoslibrary.googleapis.com/v1/albums?pageSize=50", method="GET")
        else:
            (resp, content) = req.request("https://photoslibrary.googleapis.com/v1/albums?pageSize=50&pageToken="+nextpage,method="GET")
        if resp.status is not 200:
            print('failed to load albums',resp.status,resp.reason)
            return
        data = json.loads(content)
        # remember nextpage
        if 'nextPageToken' in data:
            nextpage=data['nextPageToken']
        else:
            nextpage=None
        # store albums' data by their last download time
        for album in data['albums']:
            if album['id'] in old:
                album['path']=old[album['id']]['path']
                new[old[album['id']]['downloadTime']] = new.get(old[album['id']]['downloadTime'], [])+[album]
            elif not skip_new:
                album['path']=str(dest/album['title'])
                new[0] = new.get(0, [])+[album]
    # download albums starting from the oldest downloaded
    for k,albums in sorted(new.items()):
        for album in albums:
            # download only allowed albums
            if album['title'] not in ignore and album['id'] not in ignore:
                if album['id'] in old:
                    DowloadAlbum(req,trash,album,old[album['id']])
                else:
                    DowloadAlbum(req,trash,album,None)

# dowload album
def DowloadAlbum(req,trash,album,old_album):
    album['mediaItems']={}
    dest=Path(album['path'])
    dest.mkdir(parents=True,exist_ok=True)
    req_body=json.dumps({"albumId": album['id'],"pageSize": 100});
    req_headers={'content-type':'application/json'}
    nextpage='0'
    res=True
    while nextpage is not None:
        # request album data
        if nextpage=='0':
            (resp, content) = req.request("https://photoslibrary.googleapis.com/v1/mediaItems:search",
                                           method="POST",body=req_body,headers=req_headers)
        else:
            (resp, content) = req.request("https://photoslibrary.googleapis.com/v1/mediaItems:search?pageToken="+nextpage,
                                           method="POST",body=req_body,headers=req_headers)
        if resp.status is not 200:
            print('failed to load album',album['title'],resp.status,resp.reason)
            return False
        items = json.loads(content)
        # prepare next token
        if 'nextPageToken' in items:
            nextpage=items['nextPageToken']
        else:
            nextpage=None
        # download album media
        for media in items['mediaItems']:
            if old_album is not None and media['id'] in old_album['mediaItems']:
                res=CheckMedia(req,trash,album,media,old_album['mediaItems'][media['id']])
            else:
                res=DowloadMedia(req,trash,album,media)
            if not res:
                break
        # keep old album data
        if not res:
            if old_album is not None:
                for media in old_album['mediaItems'].items():
                    if media['id'] not in album['mediaItems']:
                        album['mediaItems'][media['id']]=media
            break
    # set album download time and store its metadata
    album['downloadTime']=datetime.now(timezone.utc).timestamp()*1000
    with (Path(album['path'])/'album.json').open("w") as file:
        json.dump(album,file,indent=2)
    # stop here if something went wrong
    if not res:
        print(album['title'],"is not completely synchronized")
        return False
    # build set of current album items filenames and remove any excess files from directory
    filenames={'album.json'}
    for id,media in album['mediaItems'].items():
        filenames.add(media['filename'])
    for file in Path(album['path']).glob('*'):
        if file.name not in filenames:
            moved=None
            if trash is not None:
                moved=common.move_file(str(file),str(trash/album['title']/file.name))
            if moved:
                print(album['title'],file.name,'excessive file moved to trash directory')
            else:
                dest.unlink()
                print(album['title'],file.name,'excessive file removed')
    # try to rename media files to original filenames
    changed=False
    for id,media in album['mediaItems'].items():
        filename=Path(album['path'],media['filename'])
        if filename.exists():
            filename=common.move_file(str(filename),NameClear(str(filename),media['id']),False)
            if filename:
                filename=Path(filename)
                if media['filename']!=filename.name:
                    print(album['title'],filename.name,'renamed from',media['filename'])
                    media['filename']=filename.name
                    changed=True
    # and store it again
    if changed:
        with (Path(album['path'])/'album.json').open("w") as file:
            json.dump(album,file,indent=2)
    # ok
    print(album['title'],"is up to date now")
    return True

# check existing media
def CheckMedia(req,trash,album,media,old):
    # fake
    if old is None or 'filename' not in old:
        return DowloadMedia(req,trash,album,media)
    # check if exists
    oldpath=Path(album['path'],old['filename'])
    if not oldpath.exists():
        return DowloadMedia(req,trash,album,media)
    # something changed
    if not common.compare_dict(media,old,{'baseUrl','filename'}):
        # move outdated file to trash directory
        moved=None
        if trash is not None:
            moved=common.move_file(str(oldpath),str(trash/album['title']/NameClear(old['filename'],old['id'])))
        if moved:
            print(album['title'],oldpath.name,'outdated file moved to trash directory')
        else:
            dest.unlink()
            print(album['title'],oldpath.name,'outdated file deleted')
        # download new media
        return DowloadMedia(req,trash,album,media)
    # set old filename
    media['filename']=old['filename']
    # store media data to album
    album['mediaItems'][media['id']]=media
    # ok
    print(album['title'],media['filename'],'already exists')
    return True

# dowload new media
def DowloadMedia(req,trash,album,media):
    # can we store it?
    if 'filename' not in media:
        return True
    # format destination
    dest=Path(album['path'],media['filename'])
    if dest.exists():
        dest=Path(NameExtend(str(dest),media['id']))
        if dest.exists():
            # move outdated file to trash directory
            moved=None
            if trash is not None:
                moved=common.move_file(str(dest),str(trash/album['title']/media['filename']))
            if moved:
                print(album['title'],dest.name,'outdated file moved to trash directory')
            else:
                dest.unlink()
                print(album['title'],dest.name,'outdated file deleted')
    # format temp filename
    tmp=dest.parent/(dest.name+'.tmp')
    # file already exists
    if tmp.exists():
        tmp.unlink()
        print(album['title'],tmp.name,'removed old temp file')
    # download media
    for t in [1,2,5,15,30,60,0]:
        (resp, content) = req.request(media['baseUrl']+'=d', method="GET")
        if resp.status is 200:
            break
        if t<=0:
            print(album['title'],tmp.name,'download failed, max attempts exceeded','[{status} {reason}]'.format(status=resp.status,reason=resp.reason))
            return False
        print(album['title'],tmp.name,'download failed, try again in',t,'seconds','[{status} {reason}]'.format(status=resp.status,reason=resp.reason))
        time.sleep(t)
    # save media
    with tmp.open('wb') as f:
        f.write(content)
    if not tmp.exists():
        print(album['title'],tmp.name,'failed to save file')
        return False
    # rename
    filename=common.move_file(str(tmp),str(dest))
    if filename:
        dest=Path(filename)
    else:
        print(album['title'],dest.name,'failed to save file')
        return False
    # save media data to album
    media['filename']=dest.name
    album['mediaItems'][media['id']]=media
    print(album['title'],media['filename'],'downloaded')
    return True

# extend filename with id <original name>_<id last 8 chars>.<original extension>
def NameExtend(name,id):
    path=Path(name)
    return str(Path(path.parent)/(path.stem+'_'+id[-8:]+path.suffix))

# remove id from filename
def NameClear(name,id):
    path=Path(name)
    # cut off id if its known
    if id and path.stem.endswith('_'+id[-8:]):
        return str(Path(path.parent)/(path.stem[:-9]+path.suffix))
    return name

# main flow
req=Authorize(Path(args.key_file),Path(args.creds_file))
old_albums=LoadAlbums(Path(args.destination))
ignore_albums=LoadIgnore(Path(args.destination))
DowloadAlbums(req,Path(args.trashbin),Path(args.destination),old_albums,ignore_albums,args.skip_new)
