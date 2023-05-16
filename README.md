# Storm
A multi-purpose tool using concurrent Tor circuits

## Example

```bash
$ python storm.py -c 4 -T /usr/bin/tor down -u http://example.onion/file.txt -o saves
```

## Requirements

You need Python 3 and a tor binary

## Usage
```
storm.py [-c CIRCUITS] [-t TMP_DIR] [-C] [-p PORT_START] [-T TOR_BIN] [-D] {ddos|down} 
  [-u TARGET_URL] [-l TARGETS_LIST] [-f TARGETS_FILE] [-o OUTPUT_DIR] [-s CHUNK_SIZE] [-m MAX_ATTEMPTS]
```
#### Common Options
```
options:
  -h, --help                show this help message and exit
  -c, --circuits N          Number of concurrent circuits to establish
  -t, --tmp-dir PATH        Dedicated temporal data directory
  -C, --change-circuits     Try using a new circuit (different IP) for each request.
  -p, --port-start PORT     Number of concurrent circuits to establish
  -T, --tor-bin PATH        Tor binary path
  -D, --debug               Print debugger messages

mode:
    ddos                    DDOS attack mode
    down                    File downloader mode
```
#### DDoS Mode Options
```
ddos options:
  -h, --help                show this help message and exit
  -u, --target-url URL      Onion URL to hit
  -l, --targets-list LIST   Comma separated list of URLs to hit
  -f, --targets-file PATH   File containing a list of file URLs to hit
```

#### Download Mode Options
```
down options:
  -h, --help                show this help message and exit
  -u, --target-url URL      Onion URL to download
  -l, --targets-list LIST   Comma separated list of URLs to download
  -f, --targets-file PATH   File containing a list of file URLs to download
  -o, --output-dir PATH     Where the downloaded files are saved
  -s, --chunk-size SIZE     Size (in bytes) of the download chunks
  -m, --max-attempts N      Max number of attempts to download a file
```
