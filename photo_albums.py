import os,sys
import argparse
import json
import time
import http.server
import socketserver
from pathlib import Path
from datetime import datetime,timezone
from requests_oauthlib import OAuth2Session
import common

# declare command line parameters
parser = argparse.ArgumentParser(description="Downloads Google Photos albums trying to prevent downloading known existing files",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument("--trashbin", default=None, help="Directory to store photos which are no longer in album")
parser.add_argument("--keys_file", help="Google API client keys json file location")
parser.add_argument("--client_id", help="Google API client id to use instead of keys file")
parser.add_argument("--client_secret", help="Google API client secret to use instead of keys file")
parser.add_argument('--redirect_proto', choices=['http', 'https'], default="http", help="Protocol to handle Google API athorization redirect")
parser.add_argument("--redirect_host", default="localhost", help="Host to handle Google API athorization redirect")
parser.add_argument("--redirect_port", choices=range(1,65535), metavar="[1-65535]", default=8080, help="Port to handle Google API athorization redirect")
parser.add_argument("--tokens_file", default="tokens.json", help="Authorized tokens json file location")
parser.add_argument('--skip_new', action='store_true', help="Skip new Google Photos albums downloading")
parser.add_argument('--headless', action='store_true', help="Run in headless mode without interactive authenfication")
parser.add_argument("destination", help="Destination download directory")
args = parser.parse_args()

# prepare authorized Google API request
# based on examples:
# https://github.com/requests/requests-oauthlib/blob/master/docs/examples/real_world_example_with_refresh.rst
# https://requests-oauthlib.readthedocs.io/en/latest/examples/google.html
# https://requests-oauthlib.readthedocs.io/en/latest/oauth2_workflow.html#third-recommended-define-automatic-token-refresh-and-update
def Authorize(args)->OAuth2Session:
    # prepare credentials
    auth_url='https://accounts.google.com/o/oauth2/auth'
    token_url='https://oauth2.googleapis.com/token'
    refresh_url=token_url
    creds={'client_id':'','client_secret':''}
    scopes=['https://www.googleapis.com/auth/photoslibrary.readonly']

    # read keys_file
    if args.keys_file:
        try:
            with open(args.keys_file,mode='r') as file:
                data=json.load(file);
                if 'installed' in data:
                    data=data['installed']
                if 'auth_uri' in data:
                    auth_url=data['auth_uri']
                if 'token_uri' in data:
                    token_url=data['token_uri']
                if 'client_id' in data:
                    creds['client_id']=data['client_id']
                if 'client_secret' in data:
                    creds['client_secret']=data['client_secret']
        except:
            print('specfied keys file',args.keys_file,'does not exist or invalid')

    # get override credentials
    if args.client_id:
        creds['client_id']=args.client_id
    if args.client_secret:
        creds['client_secret']=args.client_secret

    # check credentials
    if not creds['client_id'] or not creds['client_secret']:
        print('client id or secret are not specified')
        return None

    # try to refresh existing tokens
    if args.tokens_file and Path(args.tokens_file).exists():
        try:
            with open(args.tokens_file,mode='r') as file:
                tokens=json.load(file);
                if not set(scopes).issubset(set(tokens['scope'])):
                    raise Exception('invalid token scopes')
                api=OAuth2Session(creds['client_id'],token=tokens,auto_refresh_kwargs=creds,auto_refresh_url=refresh_url,token_updater=SaveTokens)
                tokens=api.refresh_token(refresh_url)
                SaveTokens(tokens)
                return api
        except Exception as error:
            print(error)
            print('specfied tokens file',args.tokens_file,'is invalid or expired')

    # cannot go further in headless mode
    if args.headless:
        print('can not proceed in headless mode without tokens')
        return None

    # build redirect url
    redirect_url=args.redirect_proto+'://'+args.redirect_host+':'+str(args.redirect_port)

    # auth redirection hadler
    def HttpHandle(self):
        nonlocal redirect_url
        redirect_url=self.headers.get("Host")+self.path
        self.close_connection=True
        self.send_response(code=200)
        self.end_headers()
        self.wfile.write(bytes("Return to console", "utf-8"))
        
    # silent http logger
    def HttpLogSilent(self, format, *args):
        pass
    
    # redirect user to Google for authorization
    api=OAuth2Session(creds['client_id'],scope=scopes,redirect_uri=redirect_url,auto_refresh_kwargs=creds,auto_refresh_url=refresh_url,token_updater=SaveTokens)
    user_auth_url, state = api.authorization_url(auth_url,access_type="offline",prompt="select_account")
    print('please go here and authorize:')
    print(user_auth_url)
    
    # start listening for http redirection
    handler=http.server.SimpleHTTPRequestHandler
    handler.do_GET=HttpHandle
    handler.log_message=HttpLogSilent
    
    with socketserver.TCPServer(("", args.redirect_port), handler) as httpd:
        httpd.handle_request()
    
    # fetch the access token
    if args.redirect_proto=='http':
        os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    tokens=api.fetch_token(token_url,client_secret=creds['client_secret'],authorization_response=redirect_url)
    SaveTokens(tokens)
    
    # return api
    return api

# save tokens
def SaveTokens(tokens):
    if args.tokens_file:
       with open(args.tokens_file,mode='w') as file:
           json.dump(tokens,file)

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
    print('local albums in',dest,':',len(albums))
    return albums

# load ignore list
def LoadIgnore(dest):
    ignore=[]
    for path in dest.rglob('ignore.*'):
        with path.open() as file:
            ignore+=file.read().splitlines()
    return ignore

# dowload all albums
def DowloadAlbums(api,trash,dest,old,ignore,skip_new):
    num_new=0
    num_local=0
    nextpage='0'
    new={}
    while nextpage is not None:
        # get album data
        if nextpage=='0':
            resp = api.get("https://photoslibrary.googleapis.com/v1/albums?pageSize=50")
        else:
            resp = api.api("https://photoslibrary.googleapis.com/v1/albums?pageSize=50&pageToken="+nextpage)
        if resp.status_code is not 200:
            print('failed to load albums',resp.status_code,resp.reason)
            return
        data = json.loads(resp.content)
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
                num_local+=1
            else:
                if not skip_new:
                    album['path']=str(dest/album['title'])
                    new[0] = new.get(0, [])+[album]
                num_new+=1
    print(f'Google Photos albums: {num_new+num_local} ({num_local} local, {num_new} new)%s' %(', new albums will be skipped' if skip_new else ''))
    # download albums starting from the oldest downloaded
    for k,albums in sorted(new.items()):
        for album in albums:
            # download only allowed albums
            if album['title'] not in ignore and album['id'] not in ignore:
                if album['id'] in old:
                    DowloadAlbum(api,trash,album,old[album['id']])
                else:
                    DowloadAlbum(api,trash,album,None)

# dowload album
def DowloadAlbum(api,trash,album,old_album):
    print(album['title'],'downloading to',album['path'])
    album['mediaItems']={}
    skipped=0
    dest=Path(album['path'])
    dest.mkdir(parents=True,exist_ok=True)
    req_body=json.dumps({"albumId": album['id'],"pageSize": 100});
    req_headers={'content-type':'application/json'}
    nextpage='0'
    res=True
    while nextpage is not None:
        # request album data
        if nextpage=='0':
            resp = api.post("https://photoslibrary.googleapis.com/v1/mediaItems:search",
                            data=req_body,headers=req_headers)
        else:
            resp = api.post("https://photoslibrary.googleapis.com/v1/mediaItems:search?pageToken="+nextpage,
                            data=req_body,headers=req_headers)
        if resp.status_code is not 200:
            print(album['title'],'failed retrieve media list',resp.status_code,resp.reason)
            res=False
            break
        items = json.loads(resp.content)
        # prepare next token
        if 'nextPageToken' in items:
            nextpage=items['nextPageToken']
        else:
            nextpage=None
        # download album media
        for media in items['mediaItems']:
            if old_album is not None and \
               media['id'] in old_album['mediaItems'] and \
               CheckMedia(trash,album,media,old_album['mediaItems'][media['id']]):
                skipped+=1
            elif not DowloadMedia(api,trash,album,media):
                res=False
                break
        if not res:
            break
    # journal skipped files
    if skipped>0:
        print(album['title'],skipped,'up to date media files skipped')
    # keep old album data
    if not res:
        if old_album is not None:
            for media in old_album['mediaItems'].items():
                if media['id'] not in album['mediaItems']:
                    album['mediaItems'][media['id']]=media
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
                file.unlink()
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
def CheckMedia(trash,album,media,old):
    # fake
    if old is None or 'filename' not in old:
        return False
    # check if exists
    oldpath=Path(album['path'],old['filename'])
    if not oldpath.exists():
        return False
    # something changed
    if not common.compare_dict(media,old,{'baseUrl','filename'}):
        # move outdated file to trash directory
        moved=None
        if trash is not None:
            moved=common.move_file(str(oldpath),str(trash/album['title']/NameClear(old['filename'],old['id'])))
        if moved:
            print(album['title'],oldpath.name,'outdated file moved to trash directory')
        else:
            oldpath.unlink()
            print(album['title'],oldpath.name,'outdated file deleted')
        # download new media
        return False
    # set old filename
    media['filename']=old['filename']
    # store media data to album
    album['mediaItems'][media['id']]=media
    # ok
    return True

# dowload new media
def DowloadMedia(api,trash,album,media):
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
        resp = api.get(media['baseUrl']+'=d')
        if resp.status_code is 200:
            break
        if t<=0:
            print(album['title'],tmp.name,'download failed, max attempts exceeded','[{status} {reason}]'.format(status=resp.status_code,reason=resp.reason))
            return False
        print(album['title'],tmp.name,'download failed, try again in',t,'seconds','[{status} {reason}]'.format(status=resp.status_code,reason=resp.reason))
        time.sleep(t)
    # save media
    with tmp.open('wb') as f:
        f.write(resp.content)
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
api=Authorize(args)
if api is None:
    print('Google Photo API authorization failed')
else:
    old_albums=LoadAlbums(Path(args.destination))
    ignore_albums=LoadIgnore(Path(args.destination))
    DowloadAlbums(api,
                  Path(args.trashbin) if args.trashbin else None,
                  Path(args.destination),
                  old_albums,ignore_albums,args.skip_new)
