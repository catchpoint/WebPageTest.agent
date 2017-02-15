# Copyright 2017 Google Inc. All rights reserved.
# Use of this source code is governed by the Apache 2.0 license that can be
# found in the LICENSE file.
"""Cross-platform support for os-level things that differ on different platforms"""
import logging
import platform
import subprocess

def kill_all(exe, force):
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


def launch_process(args):
    """Start a process using platform-specific support"""
    if platform.system() == 'Windows':
        import win32api
        import win32con
        import win32process
        exe = args.pop(0)
        if exe.find(' ') > -1:
            exe = '"' + exe + '"'
        command_line = exe + ' ' + ' '.join(args)
        startupinfo = win32process.STARTUPINFO()
        startupinfo.wShowWindow = 1 #SW_SHOWNORMAL
        process_handle, thread_handle, proc, thread_id = \
            win32process.CreateProcess(None,             # executable
                                       command_line,     # command-line
                                       None,             # process attributes
                                       None,             # security attributes
                                       0,                # inherit handles
                                       win32con.NORMAL_PRIORITY_CLASS,
                                       None,             # new environment
                                       None,             # current directory
                                       startupinfo)      #startupinfo
        if process_handle:
            win32api.CloseHandle(process_handle)
        if thread_handle:
            win32api.CloseHandle(thread_handle)
    else:
        proc = subprocess.Popen(args, shell=True)
    return proc


def stop_process(proc):
    """Stop a process using platform-specific support"""
    if platform.system() != 'Windows':
        proc.terminate()
        proc.kill()


def flush_dns():
    """Flush the OS DNS resolver"""
    logging.debug("Flushing DNS")
    plat = platform.system()
    if plat == "Windows":
        subprocess.call(['ipconfig', 'flushdns'])
    elif plat == "Darwin":
        subprocess.call(['sudo', 'killall', '-HUP', 'mDNSResponder'])
        subprocess.call(['sudo', 'dscacheutil', '-flushcache'])
        subprocess.call(['sudo', 'lookupd', '-flushcache'])
    elif plat == "Linux":
        subprocess.call(['sudo', 'service', 'dnsmasq', 'restart'])
        subprocess.call(['sudo', 'rndc', 'restart'])
