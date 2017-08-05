#!/usr/bin/python3

############################
## SKIP FUNCTIONALITY CODE ADAPTED FROM:
##   http://stackoverflow.com/questions/323972/is-there-any-way-to-kill-a-thread-in-python
############################

from flask import Flask, redirect, request, render_template
from flask_login import LoginManager, UserMixin, login_required, login_user, logout_user, current_user

import random
import sys
import time
import threading
from mps_youtube.commands import play
from pafy import backend_shared as pafyshared
from pafy import backend_internal as pafyinternal

import inspect
import ctypes


############################
## PARAMETERS AND SETUP
############################

app = Flask(__name__)
app.config.update(
  # DEBUG = True,
  SECRET_KEY = 'blazeit420'
)

# Performance constants
MAX_HIST_SIZE = 100
MAX_PLAYLIST_SIZE = 200
IDLE_SLEEP_SEC = 1.000

# Rules
rules = [
    ('Duration < 7 min' , lambda song: song.length_seconds < 420),
    ('Score > 4.0'      , lambda song: song.avg_rating > 4),
    ('Views > 10k'      , lambda song: song.view_count > 10000),
]


############################
## LOGIN CONFIG    TODO
############################

# container user class
class FlaskUser(UserMixin):
  def __init__(self, un, pw):
    self.un = un
    self.pw = pw
  def get_id(self):
    return self.un

# Admin account
username = 'admin'
password = 'p00password'
adminuser = FlaskUser(username, password)

# flask-login
login_manager = LoginManager()
login_manager.init_app(app)
# login_manager.login_view = "login"
login_manager.session_protection = "strong"

# callback to reload the user object
@login_manager.user_loader
def load_user(userid):
  return adminuser if userid == username else None


############################
## LOGGING
############################
_loglock = threading.RLock()
LOGFILE = 'log.txt'
def LOG(obj):
  with _loglock:
    with open(LOGFILE, 'a') as f:
      f.write('[' + time.strftime("%Y-%m-%d %H:%M:%S") + '] ' + str(obj) + '\n')


############################
## PLAYER
############################

class Player(threading.Thread):

  ############################
  ## SkipException + Song
  ############################
  class SkipException(Exception):
    pass

  class Song:
    def __init__(self, url, ipaddr):
      self.url = url
      self.ipaddr = ipaddr
      self.score = 1 #random.randint(0, 100)

      self.videoid = pafyshared.extract_video_id(url)

      vidinfo = pafyinternal.get_video_info(self.videoid, None)
      self.title = vidinfo['title'][0]
      self.length_seconds = int(vidinfo['length_seconds'][0])
      self.view_count = int(vidinfo['view_count'][0])
      self.avg_rating = float(vidinfo['avg_rating'][0])

      self.length_str = str(self.length_seconds // 60) + ':' + '%02d' % (self.length_seconds % 60)


  ############################
  ## Constructor
  ############################
  def __init__(self):
    super().__init__()
    self._playlist = []
    self._playlistlock = threading.RLock()
    self._currentlyplaying = []
    self._history = []

    # start
    self.daemon = True
    self.start()


  ############################
  ## Skip functionality
  ############################
  def _get_my_tid(self):
    if not self.isAlive():
      raise threading.ThreadError("the thread is not active")

    # do we have it cached?
    if hasattr(self, "_thread_id"):
      return self._thread_id

    # no, look for it in the _active dict
    for tid, tobj in threading._active.items():
      if tobj is self:
        self._thread_id = tid
        return tid

    raise AssertionError("could not determine the thread's id")

  ############################
  ## Main method of daemon thread
  ############################
  def run(self):
    while True:
      try:
        nextsong = self.pop()
        if nextsong:
          self._currentlyplaying = [nextsong]
          play.play_url(nextsong.url, None)
          self._history.insert(0, nextsong)
          self._history = self._history[:MAX_HIST_SIZE]
        else:
          self._currentlyplaying = []
          time.sleep(IDLE_SLEEP_SEC)
      except self.SkipException:
        if nextsong:
          self._history.insert(0, nextsong)
          self._history = self._history[:MAX_HIST_SIZE]
      except:
        LOG('Exception in run(): ' + str(sys.exc_info()[0]))

  ############################
  ## Player methods
  ############################
  def push(self, newsong, force=False):
    with self._playlistlock:
      if not force:
        # Check that is passes all rules
        for r in rules:
          if not r[1](newsong):
            return 'Error, must satisfy: ' + r[0]

        # Check for duplicates
        if (newsong.videoid in [x.videoid for x in self._playlist] or
            newsong.videoid in [x.videoid for x in self._currentlyplaying]):
          return 'Error: Duplicate'

      # Add to playlist
      self._playlist.append(newsong)
      return 'Added: ' + newsong.title

  def pop(self):
    with self._playlistlock:
      if not self._playlist:
        return None
      first = self._playlist[0]
      self._playlist = self._playlist[1:]
      return first

  def skip(self):
    tid = ctypes.c_long(self._get_my_tid())
    exctype = ctypes.py_object(self.SkipException)

    '''Raises an exception in the threads with id tid'''
    res = ctypes.pythonapi.PyThreadState_SetAsyncExc(tid, exctype)

    if res == 1:
      return 'Skipped'
    elif res == 0:
      LOG('skip(): invalid thread id')
    else:
      # "if it returns a number greater than one, you're in trouble,
      # and you should call it again with exc=NULL to revert the effect"
      ctypes.pythonapi.PyThreadState_SetAsyncExc(tid, 0)
      LOG('skip(): PyThreadState_SetAsyncExc failed')
    return 'Failed to skip. Check logs.'


############################
## PLAYER TOOLS
############################

# The global Player
player = Player()

# Public methods
def parsecommand(input_, ipaddr, isadmin=False):
  global player

  # Login
  if input_ == password:
    login_user(adminuser)
    return 'Logged in'

  # Logout
  if isadmin and input_ == 'logout':
    logout_user()
    return 'Logged out'

  # Skip command
  if isadmin and input_ == 'skip':
    return player.skip()

  # Parse URL and add song to playlist
  try:
    newsong = player.Song(input_, ipaddr)
  except (ValueError, IOError) as e:
    LOG('parsecommand(): ' + str(e))
    return 'Error: Failed to parse video URL'
  return player.push(newsong, force=isadmin)


############################
## WEB PAGES
############################

# Home page
@app.route("/", methods=['GET', 'POST'])
def main():
  global player

  # Handling input URL box
  input_  = '' if request.method != 'POST' else request.form['inputBox']
  ipaddr  = '' if request.method != 'POST' else request.environ['REMOTE_ADDR']
  message = ''

  if input_:
    message = parsecommand(input_, ipaddr, isadmin=current_user.is_authenticated)
    LOG(ipaddr + ": " + input_ + ", message=" + message)

  return render_template('index.html', inputBox=input_,
                                       message=message,
                                       playlist=player._playlist,
                                       currentlyplaying=player._currentlyplaying,
                                       history=player._history)


############################
## APPLICATION ENTRY POINT
############################
if __name__ == "__main__":
  LOG('Launching webpage . . .')
  app.run('0.0.0.0', threaded=True)
