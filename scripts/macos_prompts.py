# Trigger the various permissions prompts needed for wptagent on OSX
import json
import os
import platform
import re
import subprocess
import sys
if (sys.version_info >= (3, 0)):
    from time import monotonic
else:
    from monotonic import monotonic

if platform.system() != 'Darwin':
    print("This is only supported for MacOS")
    exit(1)

def GetSimulatorId():
    """Get the ID of a simulator to use"""
    try:
        out = subprocess.check_output(['xcrun', 'simctl', 'list', '--json', 'devices', 'available'], universal_newlines=True)
        if out:
            devices = json.loads(out)
            if 'devices' in devices:
                for runtime in devices['devices']:
                    if runtime.find('.iOS-') >= 0:
                        for device in devices['devices'][runtime]:
                            if 'udid' in device:
                                return device['udid']
    except Exception:
        pass

    print('iOS Simulator devices unavailable')
    return None

def RecordScreen():
    """Record a 100x100 area of the screen to a temp file"""
    capture_display = None
    proc = subprocess.Popen('ffmpeg -f avfoundation -list_devices true -i ""',
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    _, err = proc.communicate()
    for line in err.splitlines():
        matches = re.search(r'\[(\d+)\] Capture screen', line.decode('utf-8'))
        if matches:
            capture_display = matches.group(1)
            break
    if capture_display is not None:
        args = ['ffmpeg', '-f', 'avfoundation',
                '-i', str(capture_display),
                '-r', '10',
                '-filter:v',
                'crop={0:d}:{1:d}:{2:d}:{3:d}'.format(100, 100, 0, 0),
                '-codec:v', 'libx264rgb', '-crf', '0', '-preset', 'ultrafast',
                '/tmp/wptagent.mp4']
        ffmpeg = subprocess.Popen(args, stdin=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        if ffmpeg:
            # Wait up to 30 seconds for something to be captured
            end_time = monotonic() + 30
            started = False
            while not started and monotonic() < end_time:
                try:
                    output = ffmpeg.stderr.readline().strip()
                    if output:
                        print(output)
                        if re.search(r'\]\sn\:\s+0\s+pts\:\s+', output) is not None:
                            started = True
                        elif re.search(r'^frame=\s+\d+\s+fps=[\s\d\.]+', output) is not None:
                            started = True
                except Exception:
                    pass
            ffmpeg.terminate()
            subprocess.call(['killall', '-9', 'ffmpeg'])
            os.unlink('/tmp/wptagent.mp4')


# Launch the simulator
id = GetSimulatorId()
if id is not None:
    print('Starting the simulator...')
    subprocess.call(['xcrun', 'simctl', 'erase', id])
    subprocess.call(['xcrun', 'simctl', 'boot', id])
    subprocess.call(['xcrun', 'simctl', 'openurl', id, 'https://www.webpagetest.org/orange.html'])

print("Triggering prompts for simulator automation scripts")
subprocess.call(['open', '-W', '-a', os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', 'internal', 'support', 'osx', 'MoveSimulator.app')])
subprocess.call(['open', '-W', '-a', os.path.join(os.path.abspath(os.path.dirname(__file__)), '..', 'internal', 'support', 'osx', 'RotateSimulator.app')])

if id is not None:
    print('Terminating the simulators')
    subprocess.call(['xcrun', 'simctl', 'terminate', id, 'com.apple.mobilesafari'])
    subprocess.call(['xcrun', 'simctl', 'shutdown', 'all'])
    subprocess.call(['killall', 'Simulator'])

print("Triggering ffmpeg screen record prompt")
RecordScreen()

print('Done')