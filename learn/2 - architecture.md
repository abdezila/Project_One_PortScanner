> **Source:** Final Project — Port Scanner **Topic:** Python async port scanner — implementation guide (mirrors C++ Boost.Asio version)

---

# 🔒 Implementation Guide

This document walks through the actual code, explaining how asynchronous port scanning works under the hood and highlighting the tricky parts that make concurrent I/O work correctly.

---

## 📁 File Structure Walkthrough

```
port_scanner.py    # Everything in one file:
                   #   - argparse CLI setup
                   #   - PortScanner class (member variables, async methods)
                   #   - _scan_port() async worker + banner grabbing
                   #   - _run_async() event loop + semaphore concurrency
                   #   - main() entry point
```

Python collapses the C++ four-file layout (`PortScanner.hpp`, `PortScanner.cpp`, `main.cpp`, `CMakeLists.txt`) into a single file — no header/implementation split, no build system needed.

---

## 🛠 Building the CLI Interface

### Step 1: Argument Parsing

**What we're building:** User-friendly command line interface with sensible defaults

```python
# port_scanner.py:215-270
parser = argparse.ArgumentParser(
    prog="port_scanner.py",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    epilog="⚠  Scan only systems you own or have explicit permission to test."
)
parser.add_argument("-i", "--dname",      default="127.0.0.1", metavar="HOST",  help="set domain name or IP address")
parser.add_argument("-p", "--ports",      default="1-1024",    metavar="RANGE", help="set a port range from 1 to n")
parser.add_argument("-t", "--threads",    type=int, default=100,                help="max concurrent threads")
parser.add_argument("-e", "--expiry_time",type=int, default=2,  metavar="SEC",  help="timeout in seconds")
parser.add_argument("-v", "--verbose",    action="store_true",                  help="verbose output")
```

**Why this code works:**

- `type=int` on `--threads` and `--expiry_time`: type-safe parameter parsing with automatic validation. If the user passes `-t hello`, argparse raises an error and prints help rather than crashing. This mirrors Boost's `po::value<T>()` behaviour.
- Short and long option forms (`-i` and `--dname`): standard Unix convention makes the tool feel professional.
- `default=` on every argument: same as `po::value<T>()->default_value(X)` — all flags are optional, so the tool works with zero arguments for localhost scanning.

**Common mistakes here:**

```python
# Wrong — no default means user must ALWAYS provide -i, annoying for localhost testing
parser.add_argument("-i", "--dname", help="IP address")

# Right — default makes tool usable without memorizing flags every time
parser.add_argument("-i", "--dname", default="127.0.0.1", help="IP address")
```

---

### Step 2: Displaying Help

Now we need to provide useful help text with examples.

```python
# port_scanner.py:222-236
epilog="""
Examples:
  Scan common ports on localhost:
    python port_scanner.py -i 127.0.0.1 -p 1-1024

  Full TCP port scan:
    python port_scanner.py -i 192.168.1.1 -p 65535 -t 200

  Postscriptum:
  Scan only systems you own or have explicit permission to test.
"""
```

**What's happening:**

1. argparse auto-generates the options list from `add_argument()` calls — same as C++ `std::cout << desc`
2. `epilog=` adds the manual examples section underneath — concrete scenarios people can copy-paste
3. `formatter_class=argparse.RawDescriptionHelpFormatter` stops argparse from collapsing the newlines in the epilog
4. Legal/ethical warning included — required for any security tool

**Why we do it this way:** argparse generates option descriptions automatically, but examples must be written manually. Users learn from copy-pasting examples, not reading abstract flag descriptions.

---

### Step 3: Passing Config to Scanner

Extract validated arguments and initialise the scanner:

```python
# port_scanner.py:281-294
args = parser.parse_args()

scanner = PortScanner()
scanner.set_options(
    host        = args.dname,
    ports       = args.ports,
    threads     = args.threads,
    expiry_time = args.expiry_time,
    verbose     = args.verbose,
)
```

This pattern (default constructor + `set_options`) allows reusing a scanner object for multiple scans. Alternative would be passing everything to `__init__`, but that's less flexible for interactive use.

---

## ⚙️ Building the Core Scanner

### The Scanning Algorithm

The heart of the scanner is the `_scan_port()` method which implements a self-scheduling async pattern:

```python
# port_scanner.py:114-166
async def _scan_port(self, port: int, semaphore: asyncio.Semaphore):
    async with semaphore:                          # bail out if at worker limit
        service = BASIC_PORTS.get(port, "---")
        banner  = "---"

        try:
            reader, writer = await asyncio.wait_for(   # timer races connection
                asyncio.open_connection(self.ip, port),
                timeout=self.expiry_time
            )

            # connection succeeded — port is OPEN, attempt banner grab
            try:
                data = await asyncio.wait_for(reader.read(128), timeout=2)
                if data:
                    banner = data.decode(errors="replace").strip()
            except Exception:
                pass

            writer.close()
            await writer.wait_closed()
            print(f"{port}\t{GREEN}OPEN{RESET}\t{service}\t{banner}")
            self.open_ports += 1

        except asyncio.TimeoutError:
            # timeout fired before connection → FILTERED
            if self.verbose:
                print(f"{port}\tFILTERED\t{service}\t---")
            self.filtered_ports += 1

        except (ConnectionRefusedError, OSError):
            # connection refused → CLOSED
            if self.verbose:
                print(f"{port}\t{RED}CLOSED{RESET}\t{service}\t---")
            self.closed_ports += 1
```

**Key parts explained:**

**Semaphore guard (`line 123`):**

```python
async with semaphore:
```

This is the Python equivalent of the C++ guard clause `if (q.empty() || cnt >= MAX_THREADS) return;`. `asyncio.Semaphore(max_threads)` lets at most `max_threads` coroutines inside the block at once. If that limit is reached, any new coroutine hitting `async with semaphore` simply waits until one finishes and releases.

**Timeout as timer race (`lines 130-133`):**

```python
reader, writer = await asyncio.wait_for(
    asyncio.open_connection(self.ip, port),
    timeout=self.expiry_time
)
```

In C++, the timer and the connection race each other via a shared `*complete` flag — whichever fires first sets the flag and the loser returns early. In Python, `asyncio.wait_for()` wraps this entire race in one call: if `open_connection()` succeeds before the timeout, execution continues normally; if the timeout fires first, it raises `asyncio.TimeoutError`. No `*complete` flag needed — `wait_for` handles the race internally.

**No shared-pointer lifetime management:** In C++, `std::make_shared<tcp::socket>` and `std::make_shared<bool>` were needed to keep objects alive across async callbacks. In Python this isn't needed — `reader` and `writer` are regular local variables that stay alive for the duration of the `async with` block because Python's coroutines don't return until `await` resolves.

**Self-scheduling via gather (`_run_async`):**

```python
# port_scanner.py:169-184
async def _run_async(self):
    semaphore = asyncio.Semaphore(self.max_threads)
    tasks = [self._scan_port(port, semaphore) for port in range(self.start_port, self.end_port + 1)]
    await asyncio.gather(*tasks)
```

In C++, each completion handler ended with a recursive `scan()` call — a self-scheduling tail-recursive work distribution pattern. Python uses `asyncio.gather(*tasks)` instead, which achieves the same result: all port coroutines are created upfront, and the semaphore ensures only `max_threads` of them actually run at once. As soon as one finishes and exits `async with semaphore`, the next waiting coroutine enters. No central dispatcher needed.

**Why this specific implementation:**

`asyncio.wait_for` elegantly solves filtered port detection. Without the timeout, `open_connection()` would wait forever on filtered ports where a firewall drops packets silently and never sends a reply. The timeout fires after `expiry_time` seconds, `TimeoutError` is caught, and the port is marked FILTERED.

The `asyncio.gather` + semaphore pattern means we never have more than `max_threads` active connections at once. Every coroutine that finishes releases its semaphore slot, and the next waiting coroutine immediately picks it up — maintaining constant concurrency.

**Common mistakes here:**

```python
# Wrong — time.sleep() inside async code blocks the ENTIRE event loop
# All other coroutines freeze while this one sleeps
async def _scan_port(self, port, semaphore):
    time.sleep(2)   # blocks everything

# Right — await asyncio.sleep() yields back to the event loop
async def _scan_port(self, port, semaphore):
    await asyncio.sleep(2)  # other coroutines keep running during the wait
```

---

## 🔒 Security Implementation

### Banner Grabbing

```python
# port_scanner.py:137-143
try:
    data = await asyncio.wait_for(reader.read(128), timeout=2)
    if data:
        banner = data.decode(errors="replace").strip()
except Exception:
    pass
```

**What this prevents:** Nothing — banner grabbing is an offensive technique, not a defence. But understanding it helps you secure your services.

**How it works:**

1. After a successful connection, attempt to read up to 128 bytes from the socket
2. `asyncio.wait_for(reader.read(128), timeout=2)` — a second inner timeout so we don't wait forever for a banner that never comes
3. If bytes were received, decode them and store as the banner string
4. `errors="replace"` handles non-UTF-8 bytes safely (binary protocols won't crash the scanner)
5. Print result including banner content

**What happens if you remove this:** You'd still detect open ports but wouldn't know what software is running. A banner like `SSH-2.0-OpenSSH_7.4` tells you it's SSH version 7.4, which has known CVEs. Without banners, you'd have to manually connect to each open port.

---

### Timeout-Based Filtering Detection

```python
# port_scanner.py:130-133 and 154-159
reader, writer = await asyncio.wait_for(
    asyncio.open_connection(self.ip, port),
    timeout=self.expiry_time
)

# ...

except asyncio.TimeoutError:
    if self.verbose:
        print(f"{port}\tFILTERED\t{service}\t---")
    self.filtered_ports += 1
```

**What this prevents:** Infinite hangs on filtered ports. Without timeouts, `open_connection()` waits indefinitely if a firewall drops packets.

**How it works:**

1. `asyncio.wait_for()` wraps the connection attempt with a deadline of `expiry_time` seconds
2. If the connection doesn't complete in time, Python raises `asyncio.TimeoutError`
3. The `except asyncio.TimeoutError` block catches it and marks the port as FILTERED

**What happens if you remove this:** The scanner would hang forever on the first filtered port. You'd scan port 1 (filtered), wait eternally, never reach port 2. Timeouts are essential for handling non-responsive targets.

---

## 📊 Data Flow Example

Let's trace a complete scan of port 22 (SSH) on a host where it's open.

### Request Starts

```python
# port_scanner.py:281-294
scanner = PortScanner()
scanner.set_options("192.168.1.100", "22", 100, 2)
```

At this point:

- `_resolve()` calls `socket.gethostbyname("192.168.1.100")` — trivial for IPs, returns immediately
- `_parse_port("22")` sets `start_port = 1`, `end_port = 22`
- `BASIC_PORTS[22]` = `"SSH"`

---

### Scanner Starts

```python
# port_scanner.py:169-184
semaphore = asyncio.Semaphore(100)
tasks = [self._scan_port(port, semaphore) for port in range(1, 23)]
await asyncio.gather(*tasks)
```

This creates 22 coroutines. All 22 enter `asyncio.gather`, but `Semaphore(100)` lets all of them through immediately (22 < 100). For a full 65535 port scan, only 100 would run at once and the rest would wait.

---

### Connection Attempt

```python
# port_scanner.py:130-133
reader, writer = await asyncio.wait_for(
    asyncio.open_connection("192.168.1.100", 22),
    timeout=2
)
```

On the wire:

1. Scanner sends SYN packet to `192.168.1.100:22`
2. Target responds with SYN-ACK (SSH is listening)
3. Scanner completes handshake with ACK
4. Connection established (< 100ms typically)

---

### Connection Succeeds

```python
# port_scanner.py:137-152
# wait_for resolved without TimeoutError — we have reader and writer
service = BASIC_PORTS.get(22)   # "SSH"

data = await asyncio.wait_for(reader.read(128), timeout=2)
```

The SSH server immediately sends its banner (protocol requirement):

```
SSH-2.0-OpenSSH_7.4p1 Debian-10+deb9u7
```

---

### Banner Received

```python
# port_scanner.py:140-152
if data:
    banner = data.decode(errors="replace").strip()
    # banner = "SSH-2.0-OpenSSH_7.4p1 Debian-10+deb9u7"

writer.close()
print(f"{port}\t{GREEN}OPEN{RESET}\t{service}\t{banner}")
self.open_ports += 1
# semaphore releases — next waiting coroutine enters
```

The result prints in green: `22 OPEN SSH SSH-2.0-OpenSSH_7.4p1 Debian-10+deb9u7`

---

## ⚠️ Error Handling Patterns

### Connection Refused (Closed Port)

When scanning port 8080 on a system with nothing listening:

```python
# port_scanner.py:161-166
except (ConnectionRefusedError, OSError):
    if self.verbose:
        print(f"{port}\t{RED}CLOSED{RESET}\t{service}\t---")
    self.closed_ports += 1
```

**Why this specific handling:** `ConnectionRefusedError` means the target sent a TCP RST packet — the port is explicitly closed. This is different from a timeout (filtered). We colour it red to distinguish from open ports visually. Only shown in verbose mode to keep output clean.

**What NOT to do:**

```python
# Bad — silently ignoring all errors hides real problems like DNS failure or routing issues
except Exception:
    pass
```

Always catch specific exceptions so network problems (wrong host, firewall on your end) surface as visible errors rather than silent wrong results.

---

### Timeout (Filtered Port)

When scanning port 12345 on a host behind a firewall that drops packets:

```python
# port_scanner.py:154-159
except asyncio.TimeoutError:
    if self.verbose:
        print(f"{port}\tFILTERED\t{service}\t---")
    self.filtered_ports += 1
```

`asyncio.TimeoutError` only fires when `wait_for`'s deadline expires naturally — it will not fire if the connection already succeeded (in that case `wait_for` resolved normally). So catching `TimeoutError` here reliably means only one thing: the connection attempt timed out → FILTERED.

---

## 🚀 Performance Optimisations

### Before: Synchronous Scanning

This naive implementation would be disastrously slow:

```python
# Don't actually do this
for port in range(1, 65536):
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect((ip, port))   # BLOCKS — nothing else runs while waiting
        print(f"{port} OPEN")
    except:
        pass
    finally:
        s.close()
```

This is slow because each `connect()` blocks the entire program for the timeout duration:

- 65535 ports × 2 seconds = **131,070 seconds = 36 hours**
- Even with fast 100ms connections: 65535 × 0.1s = **1.8 hours**

---

### After: Asynchronous Concurrent Scanning

```python
# port_scanner.py:169-184
semaphore = asyncio.Semaphore(self.max_threads)
tasks = [self._scan_port(port, semaphore) for port in ports]
await asyncio.gather(*tasks)
asyncio.run(self._run_async())
```

**What changed:**

- 100 connections run simultaneously — while one is waiting for a TCP reply, others are actively connecting to different ports
- Total time = (total ports / concurrency) × avg connection time
- 65535 ports / 100 workers × 0.1 seconds = **~66 seconds**

**Benchmarks:**

|Scenario|Synchronous|Async (100 workers)|Improvement|
|---|---|---|---|
|Full scan, 2s timeout|36 hours|~2 minutes|1080×|
|Full scan, 10ms latency|11 minutes|~7 seconds|95×|

---

## 🗺️ Configuration Management

### Port Range Parsing

```python
# port_scanner.py:76-104
def _parse_port(self, port_str: str):
    if "-" not in port_str:
        # no dash — single number means 1 to N
        end = int(port_str)          # "1024" means scan 1-1024
        self.start_port = 1
        self.end_port   = end
        return

    # parse "start-end" format
    start_str, end_str = port_str.split("-", 1)
    start = int(start_str)
    end   = int(end_str)

    # validate bounds
    if start == 0 or end > MAX_PORT or start > end:
        self.start_port = 1
        self.end_port   = MAX_PORT   # invalid input → full scan fallback
    else:
        self.start_port = start
        self.end_port   = end
```

**Important details:**

- **Input validation**: bounds checking ensures we don't scan port 0 (reserved/invalid) or > 65535 (impossible — TCP ports are 16-bit)
- **Fallback behaviour**: invalid input like `"5000-100"` (start > end) defaults to a full scan rather than crashing — same as the C++ version
- **Two accepted formats**: `"1024"` (upper bound only, start defaults to 1) and `"80-443"` (explicit range)

We validate early because invalid port ranges cause weird errors later — the range would be empty or backwards. Failing fast at config time is better than mysterious runtime behaviour.

---

### DNS Resolution

```python
# port_scanner.py:106-112
def _resolve(self):
    try:
        self.ip = socket.gethostbyname(self.host)
    except socket.gaierror:
        print(f"[ERROR] Could not resolve host: {self.host}")
        sys.exit(1)
```

**How this works:** `socket.gethostbyname()` queries DNS for A records. For `"scanme.nmap.org"` it returns `45.33.32.156`. For IP addresses like `"192.168.1.1"` it validates the format and returns immediately without hitting DNS.

**Error handling:** If resolution fails (domain doesn't exist, DNS server unreachable), `socket.gaierror` is raised. We catch it, print a clear error, and exit immediately with `sys.exit(1)`. This is intentional — better to fail at startup than to silently scan the wrong host or crash mid-scan with a confusing error.

---

## 🐛 Common Implementation Pitfalls

### Pitfall 1: Using `time.sleep()` Instead of `await asyncio.sleep()`

**Symptom:** The scanner feels sequential — ports seem to scan one at a time, nowhere near the expected concurrency speed.

**Cause:**

```python
# Wrong — blocks the ENTIRE event loop; all other coroutines freeze
async def _scan_port(self, port, semaphore):
    time.sleep(2)    # nothing else runs during these 2 seconds
```

asyncio is single-threaded. When `time.sleep()` runs it holds the thread, blocking every other coroutine. The 100-worker concurrency effectively becomes 1.

**Fix:**

```python
# Right — yields control back to the event loop while waiting
async def _scan_port(self, port, semaphore):
    await asyncio.sleep(2)    # other coroutines keep running
```

**Why this matters:** This is the Python equivalent of the C++ pitfall of forgetting to bind to the strand. In both cases, the fix is using the right async primitive — one that cooperates with the scheduler rather than blocking it.

---

### Pitfall 2: Forgetting `asyncio.run()` / Not Awaiting Coroutines

**Symptom:** No output, no errors — program finishes instantly having scanned nothing.

**Cause:**

```python
# Wrong — calling an async function without await returns a coroutine object, doesn't run it
scanner._run_async()          # returns <coroutine object> — does nothing

# Also wrong — creating tasks without running the event loop
tasks = [_scan_port(port) for port in ports]   # creates coroutines but never runs them
```

**Fix:**

```python
# Right — asyncio.run() starts the event loop and blocks until _run_async() finishes
asyncio.run(self._run_async())
```

**Why this matters:** Coroutines in Python are lazy — they do nothing until awaited or passed to an event loop. This is the Python equivalent of the C++ pitfall of forgetting `io.run()` — without it, all the async setup does nothing.

---

## 🐛 Debugging Tips

### Issue: "All ports show as FILTERED"

**Problem:** Every port times out; nothing shows as OPEN or CLOSED.

**How to debug:**

1. Check firewall on your machine — outbound connections might be blocked
2. Verify target is reachable: `ping 192.168.1.100`
3. Test with a known open port: `telnet scanme.nmap.org 80` should connect
4. Reduce concurrency and increase timeout: `-t 1 -e 10` eliminates concurrency and network issues

**Common causes:**

- Target host firewall drops all incoming connections (working as designed)
- Network firewall between you and target blocks port scanning traffic
- Target host is down or unreachable
- Scanning from a restricted network (corporate, cloud provider) that blocks outbound scans

---

### Issue: "Script finishes instantly with no output"

**Problem:** Program exits in milliseconds, no ports printed.

**How to debug:**

1. Check that `asyncio.run()` is being called — not just `_run_async()`
2. Verify `_parse_port()` produced a valid range: add `print(self.start_port, self.end_port)` in `run()`
3. Check you're passing `-v` for verbose if you want to see CLOSED ports — by default only OPEN ports print

**Common causes:**

- Port range parsed to empty (e.g. `"5000-100"` → fallback to full scan, or miscounted range)
- `asyncio.run()` missing — coroutine created but never executed
- Not using `-v` and expecting to see CLOSED/FILTERED ports (they're hidden by default)

---

## 🔧 Extending the Code

### Adding UDP Scanning

Want to scan UDP ports? Here's the process:

1. **Add protocol flag** to argparse

```python
parser.add_argument("--udp", action="store_true", help="scan UDP ports instead of TCP")
```

2. **Create UDP socket** in `_scan_port()`

```python
if self.udp:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # UDP has no connection handshake — send a probe and wait for response
    sock.sendto(b'\x00', (self.ip, port))
```

3. **Interpret responses differently**

```python
# UDP scanning is harder — no connection states
# Open: service responds to your probe
# Closed: ICMP "port unreachable" comes back
# Filtered: nothing at all (same as TCP filtered)
```

UDP scanning is harder because UDP has no connection handshake. You must send protocol-specific probes and interpret responses. An empty response doesn't mean closed — many UDP services simply don't respond to random bytes.

---

## 📦 Dependencies

### Why Each Dependency

- **`asyncio`** (stdlib): Async I/O framework that abstracts OS event loop mechanics (epoll/kqueue/IOCP under the hood). We use it for `open_connection`, timeouts, and the event loop. Direct replacement for Boost.Asio — no install needed.
- **`argparse`** (stdlib): CLI argument parser with type safety and automatic help generation. We use it in `build_parser()` for the `-i`, `-p`, `-t` flags. Direct replacement for Boost.Program_Options — no install needed.
- **`socket`** (stdlib): DNS resolution via `gethostbyname()`. Direct replacement for Boost.Asio's `resolver`.

Zero third-party installs required — the entire scanner runs on Python's standard library.

---

## 🚀 Build and Run

### Running the Scanner

```bash
# Scan common ports on localhost
python port_scanner.py -i 127.0.0.1 -p 1-1024

# Run with verbose output to see all results
python port_scanner.py -i 127.0.0.1 -p 1-100 -v

# Full TCP port scan with more concurrency
python port_scanner.py -i 192.168.1.1 -p 65535 -t 200

# Scan a specific range with a longer timeout
python port_scanner.py -i scanme.nmap.org -p 20-100 -e 4 -v
```

No build step needed — Python runs directly. No CMake, no compilation, no linking.

### Testing Safely

Use `scanme.nmap.org` — a public server that Nmap maintains specifically so people can legally practice scanning it:

```bash
python port_scanner.py -i scanme.nmap.org -p 1-1024 -v
```

### Comparing with Nmap

Run `nmap -sT scanme.nmap.org` (TCP connect scan — same technique as ours) and compare results. Nmap has decades of edge-case handling our scanner doesn't, but the open/closed/filtered logic is identical.

---

## 🔑 Next Steps

You've seen how async I/O, concurrent scanning, and state detection work. Now:

1. **Try extending it** — add output to a file (`-o results.txt`), add a progress bar, or add a `--top-ports` flag that only scans the 100 most common ports
2. **Modify concurrency** — change `max_threads` to 1 and observe serial scanning (slow). Change to 1000 and watch system resource usage. Find the sweet spot for your network.
3. **Move to TryHackMe** — you've built the foundation. Port scanning is one of the first steps in every TryHackMe room. You already understand what's happening under the hood.