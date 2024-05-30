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
parser = argparse.ArgumentParser(description="Downloads Google Photos albums with photos, trying to use original photos from specified directory",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument("--originals", default=None, help="Directory with original photos")
parser.add_argument("--trashbin", default=None, help="Directory to store photos which are no longer in album")
parser.add_argument("--key_file", default="key.json", help="Application keys file location")
parser.add_argument("--creds_file", default="credentials.json", help="User credentials file location")
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
            albums[data['id']]=data
    ignore=[]
    for path in dest.rglob('ignore.*'):
        with path.open() as file:
            ignore.append(file.read().splitlines())
    return(albums,ignore)

# dowload all albums
def DowloadAlbums(req,orig,trash,dest,old,ignore):
    nextpage='0'
    new={}
    while nextpage is not None:
        if nextpage=='0':
            (resp, content) = req.request("https://photoslibrary.googleapis.com/v1/albums", method="GET")
        else:
            (resp, content) = req.request("https://photoslibrary.googleapis.com/v1/albums?pageToken="+nextpage,method="GET")
        if resp.status is not 200:
            print('failed to load albums',resp.status,resp.reason)
            return
        data = json.loads(content)
        if 'nextPageToken' in data:
            nextpage=data['nextPageToken']
        else:
            nextpage=None
        for album in data['albums']:
            if album['id'] in old and 'downloadTime' in old[album['id']]:
                album['path']=old[album['id']]['path']
                new[old[album['id']]['downloadTime']] = new.get(old[album['id']]['downloadTime'], [])+[album]
            else:
                album['path']=str(dest/album['title'])
                new[0] = new.get(0, [])+[album]
#    del new[0]
    for k,albums in sorted(new.items()):
        for album in albums:
            if album['title'] not in ignore and album['id'] not in ignore:
                if album['id'] in old:
                    DowloadAlbum(req,orig,trash,album,old[album['id']])
                else:
                    DowloadAlbum(req,orig,trash,album,None)

# dowload album
def DowloadAlbum(req,orig,trash,album,old_album):
    album['mediaItems']={}
    dest=Path(album['path'])
    dest.mkdir(parents=True,exist_ok=True)
    nextpage='0'
    while nextpage is not None:
        if nextpage=='0':
            (resp, content) = req.request("https://photoslibrary.googleapis.com/v1/mediaItems:search",method="POST",
                                           body=json.dumps({"albumId": album['id']}),
                                           headers={'content-type':'application/json'})
        else:
            (resp, content) = req.request("https://photoslibrary.googleapis.com/v1/mediaItems:search?pageToken="+nextpage,method="POST",
                                           body=json.dumps({"albumId": album['id']}),
                                           headers={'content-type':'application/json'})
        if resp.status is not 200:
            print('failed to load album',album['title'],resp.status,resp.reason)
            return False
        items = json.loads(content)
        if 'nextPageToken' in items:
            nextpage=items['nextPageToken']
        else:
            nextpage=None
        for media in items['mediaItems']:
            if old_album is not None and media['id'] in old_album['mediaItems']:
                res=DowloadMedia(req,orig,trash,album,media,old_album['mediaItems'][media['id']])
            else:
                res=DowloadMedia(req,orig,trash,album,media,None)
            if not res:
                return False
    # set album download time and store its metadata
    album['downloadTime']=datetime.now(timezone.utc).timestamp()*1000
    with (Path(album['path'])/'album.json').open("w") as file:
        json.dump(album,file,indent=2)
    # build set of current album items filenames and remove any excess files from directory
    filenames={'album.json'}
    for id,media in album['mediaItems'].items():
        filenames.add(media['filename'])
    for file in Path(album['path']).glob('*'):
        if file.name not in filenames:
            moved=False
            if trash is not None:
                moved=common.move_file(str(file),str(trash/album['title']/file.name))
            if moved:
                print(album['title'],file.name,'excessive file moved to trash directory')
            else:
                dest.unlink()
                print(album['title'],file.name,'excessive file deleted')
    return True

# dowload media
def DowloadMedia(req,orig,trash,album,media,old):
    if 'mimeType' in media and media['mimeType'].startswith('image'):
        return DowloadPhoto(req,orig,trash,album,media,old)
    return True

# dowload photo
def DowloadPhoto(req,orig,trash,album,photo,old,idx=0):
    if idx>32:
        return False
    dest=Path(album['path'])/photo['filename']
    if idx!=0:
        dest=Path("{1}_copy{2}{3}".format(dest.stem,idx,dest.suffix))
    # file already exists
    if dest.exists():
        if old is not None and common.compare_dict(photo,old,{'baseUrl'}):
            # if nothing is changed since last download, just remember item
            photo['filename']=dest.name
            album['mediaItems'][photo['id']]=photo
            print(album['title'],photo['filename'],'already exists')
            return True
        else:
            # there is different media with the same filename in album, try with different number
            for (k,dup) in album['mediaItems'].items():
                if dup['filename']==dest.name:
                    return DowloadPhoto(req,orig,trash,album,photo,old,idx+1)
            # move existing file to trash directory
            moved=False
            if trash is not None:
                moved=common.move_file(str(dest),str(trash/album['title']/photo['filename']))
            if moved:
                print(album['title'],dest.name,'excessive file moved to trash directory')
            else:
                dest.unlink()
                print(album['title'],dest.name,'excessive file deleted')
    # download photo
    for t in [1,2,5,15,30,60,sys.maxsize]:
        (resp, content) = req.request(photo['baseUrl']+'=d', method="GET")
        if resp.status is 200:
            break
        if t is sys.maxsize:
            print(album['title'],dest.name,'download failed, max attempts exceeded','[{status} {reason}]'.format(status=resp.status,reason=resp.reason))
            return False
        print(album['title'],dest.name,'download failed, try again in',t,'seconds','[{status} {reason}]'.format(status=resp.status,reason=resp.reason))
        time.sleep(t)
    # save photo
    with dest.open('wb') as f:
        f.write(content)
    # save metadata
    photo['filename']=dest.name
    album['mediaItems'][photo['id']]=photo
    print(album['title'],photo['filename'],'downloaded')
    return True


# main flow
req=Authorize(Path(args.key_file),Path(args.creds_file))
old_albums,ignore_albums=LoadAlbums(Path(args.destination))
DowloadAlbums(req,Path(args.originals),Path(args.trashbin),Path(args.destination),old_albums,ignore_albums)
