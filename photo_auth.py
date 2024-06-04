import argparse
import json
import os
import http.server
import socketserver
from requests_oauthlib import OAuth2Session

# based on example https://requests-oauthlib.readthedocs.io/en/latest/examples/google.html

# Google API OAuth endpoints given in the Google API documentation
auth_url = "https://accounts.google.com/o/oauth2/auth"
token_url = "https://oauth2.googleapis.com/token"

# auth data
client_id=''
client_secret=''
scopes=['https://www.googleapis.com/auth/photoslibrary.readonly']
redirect_url=''

# declare command line parameters
parser = argparse.ArgumentParser(description="Authorizes Google API client to access user's data as a result provides tokens for external headless service",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument("--keys_file", help="Google API client keys json file location")
parser.add_argument("--client_id", help="Google API client id to use instead of keys file")
parser.add_argument("--client_secret", help="Google API client secret to use instead of keys file")
parser.add_argument('--redirect_proto', choices=['http', 'https'], default="http", help="Protocol to handle Google API athorization redirect")
parser.add_argument("--redirect_host", default="localhost", help="Host to handle Google API athorization redirect")
parser.add_argument("--redirect_port", choices=range(1,65535), default=8080, help="Port to handle Google API athorization redirect")
parser.add_argument("tokens_file", nargs='?', default=None, help="Destination json file to store obtained tokens")
args = parser.parse_args()

# loads authorization data 
def LoadKeys(keys_file: str):
    global auth_url,token_url,client_id,client_secret
    try:
        with open(keys_file) as f:
            data = json.load(f)
            if 'installed' in data:
                data=data['installed']
            if 'auth_uri' in data:
                auth_url=data['auth_uri']
            if 'token_uri' in data:
                token_url=data['token_uri']
            if 'client_id' in data:
                client_id=data['client_id']
            if 'client_secret' in data:
                client_secret=data['client_secret']
    except:
        print("failed to load keys file")

# auth redirection hadler
def HttpHandle(self):
    global redirect_url
    redirect_url=self.headers.get("Host")+self.path
    self.close_connection=True
    self.send_response(code=200)
    self.end_headers()
    self.wfile.write(bytes("Go to console for your tokens", "utf-8"))
    
# silent http logger
def HttpLogSilent(self, format, *args):
    pass

# auth credentials
if args.client_id:
    client_id=args.client_id
if args.client_secret:
    client_secret=args.client_secret
if args.keys_file and os.path.exists(args.keys_file):
    LoadKeys(args.keys_file)
# build redirect url
redirect_url=args.redirect_proto+'://'+args.redirect_host+':'+str(args.redirect_port)

# Redirect user to Google for authorization
google = OAuth2Session(client_id, scope=scopes, redirect_uri=redirect_url)
user_auth_url, state = google.authorization_url(auth_url,access_type="offline",prompt="select_account")
print('Please go here and authorize:')
print(user_auth_url)

# start listening for http redirection
handler=http.server.SimpleHTTPRequestHandler
handler.do_GET=HttpHandle
handler.log_message=HttpLogSilent

with socketserver.TCPServer(("", args.redirect_port), handler) as httpd:
    httpd.handle_request()

# Fetch the access token
if args.redirect_proto=='http':
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
token=google.fetch_token(token_url,client_secret=client_secret,authorization_response=redirect_url)

# print tokens
print('Use this token for your headless app authorization:')
print(json.dumps(token))

#store tokens
if args.tokens_file:
    with os.open(args.tokens_file) as f:
        json.dump(token,f)
    print('Saved to',args.tokens_file)
