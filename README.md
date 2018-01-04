# window-tracker-x11
Track window/app usage when using X11 into a log file


## Installation
use 

```
sudo pip3 install  window_tracker_x11 --no-binary :all:
```

to install. (`--no-binary` is required so that data files with absolute paths are installed correctly, see [bug](https://stackoverflow.com/questions/40588634/how-to-install-data-files-to-absolute-path).)

## Usage
Then to start the service:

```
systemctl --user start window-tracker-x11.service
```

Window usage will then be logged into `$HOME/.config/window-tracker-x11/log.csv`.

To enable the service permanently:

```
systemctl --user enable window-tracker-x11.service
```

## Configuration

You can configure idle time and a few other options in `$HOME/.config/window-tracker-x11/config.py`. A sample configuration can be found in `/usr/lib/python3.6/site-packages/window_tracker_x11/config.py.sample`.
