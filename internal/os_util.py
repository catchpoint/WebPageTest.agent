# Copyright 2019 WebPageTest LLC.
# Copyright 2017 Google Inc.
# Copyright 2020 Catchpoint Systems Inc.
# Use of this source code is governed by the Polyform Shield 1.0.0 license that can be
# found in the LICENSE.md file.
"""Cross-platform support for os-level things that differ on different platforms"""
import logging
import os
import platform
import subprocess
from shutil import rmtree
import socket


def kill_all(exe, force, timeout=30):
    """Terminate all instances of the given process"""
    logging.debug("Terminating all instances of %s", exe)
    plat = platform.system()
    if plat == "Windows":
        if force:
            subprocess.call(['taskkill', '/F', '/T', '/IM', exe])
        else:
            subprocess.call(['taskkill', '/IM', exe])
    elif plat == "Linux" or plat == "Darwin":
        if force:
            subprocess.call(['killall', '-s', 'SIGKILL', exe])
        else:
            subprocess.call(['killall', exe])
    wait_for_all(exe, timeout)

def wait_for_all(exe, timeout=30):
    """Wait for the given process to exit"""
    import psutil
    processes = []
    for proc in psutil.process_iter():
        try:
            pinfo = proc.as_dict(attrs=['pid', 'name', 'exe'])
        except psutil.NoSuchProcess:
            pass
        else:
            if 'exe' in pinfo and pinfo['exe'] is not None and\
                    os.path.basename(pinfo['exe']) == exe:
                processes.append(proc)
    if len(processes):
        logging.debug("Waiting up to %d seconds for %s to exit", timeout, exe)
        psutil.wait_procs(processes, timeout=timeout)

def flush_dns():
    """Flush the OS DNS resolver"""
    logging.debug("Flushing DNS")
    plat = platform.system()
    if plat == "Windows":
        run_elevated('ipconfig', '/flushdns')
    elif plat == "Darwin":
        subprocess.call(['sudo', 'killall', '-HUP', 'mDNSResponder'])
        subprocess.call(['sudo', 'dscacheutil', '-flushcache'])
        subprocess.call(['sudo', 'lookupd', '-flushcache'])
    elif plat == "Linux":
        subprocess.call(['sudo', 'service', 'dnsmasq', 'restart'])
        subprocess.call(['sudo', 'rndc', 'restart'])
        subprocess.call(['sudo', 'systemd-resolve', '--flush-caches'])

# pylint: disable=E0611,E0401
def run_elevated(command, args, wait=True):
    """Run the given command as an elevated user and wait for it to return"""
    ret = 1
    try:
        if command.find(' ') > -1:
            command = '"' + command + '"'
        if platform.system() == 'Windows':
            import win32api
            import win32con
            import win32event
            import win32process
            from win32com.shell.shell import ShellExecuteEx
            from win32com.shell import shellcon
            logging.debug(command + ' ' + args)
            process_info = ShellExecuteEx(nShow=win32con.SW_HIDE,
                                        fMask=shellcon.SEE_MASK_NOCLOSEPROCESS,
                                        lpVerb='runas',
                                        lpFile=command,
                                        lpParameters=args)
            if wait:
                win32event.WaitForSingleObject(process_info['hProcess'], 600000)
                ret = win32process.GetExitCodeProcess(process_info['hProcess'])
                win32api.CloseHandle(process_info['hProcess'])
            else:
                ret = process_info
        else:
            logging.debug('sudo ' + command + ' ' + args)
            ret = subprocess.call('sudo ' + command + ' ' + args, shell=True)
    except Exception:
        logging.exception('Error running elevated command: %s', command)
    return ret

def wait_for_elevated_process(process_info):
    if platform.system() == 'Windows' and 'hProcess' in process_info:
        import win32api
        import win32con
        import win32event
        import win32process
        win32event.WaitForSingleObject(process_info['hProcess'], 600000)
        ret = win32process.GetExitCodeProcess(process_info['hProcess'])
        win32api.CloseHandle(process_info['hProcess'])
    return ret
# pylint: enable=E0611,E0401

# pylint: disable=E1101
def get_free_disk_space():
    """Return the number of bytes free on the given disk in Gigabytes (floating)"""
    path = os.path.dirname(os.path.realpath(__file__))
    if platform.system() == 'Windows':
        import ctypes
        free_bytes = ctypes.c_ulonglong(0)
        ctypes.windll.kernel32.GetDiskFreeSpaceExW(ctypes.c_wchar_p(path),
                                                   None, None, ctypes.pointer(free_bytes))
        return float(free_bytes.value / 1024 / 1024) / 1024.0
    else:
        stat = os.statvfs(path)
        return float(stat.f_bavail * stat.f_frsize / 1024 / 1024) / 1024.0
# pylint: enable=E1101

def get_file_version(filename):
    version = 0.0
    try:
        from win32api import GetFileVersionInfo, LOWORD, HIWORD
        info = GetFileVersionInfo (filename, "\\")
        ms = info['FileVersionMS']
        ls = info['FileVersionLS']
        version = '{0}.{1}.{2}.{3}'.format(HIWORD(ms), LOWORD(ms), HIWORD(ls), LOWORD(ls))
    except:
        logging.exception('Error getting file version for %s', filename)
    return version

def remove_file(_file):
    """ Function to handle removing a single file"""
    logging.debug("Removing File %s", _file, stacklevel=3)
    try:
        if os.path.isfile(_file):
            os.remove(_file)
    except Exception:
        pass

def remove_dir_tree(_dir):
    """ Function to remove a entire directory and the files within"""
    logging.debug("Removing Folder %s", _dir, stacklevel=3)
    try:
        if os.path.isdir(_dir):
            rmtree(_dir)
    except Exception:
        pass 
        
def pc_name():
    """ Grabs the hostname and Local IP address of the machine and returns hostname-IP """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return socket.gethostname() + "-" + s.getsockname()[0]
    except Exception as e:
        logging.error("Error getting pc_name: ", e)
        
    return platform.uname()[1]

def makeDirs(_dir: str):
    logging.debug("Creating Dir %s", _dir)
    try:
        if _dir != "" and not os.path.isdir(_dir):
            os.makedirs(_dir)
    except Exception as e:
        pass
