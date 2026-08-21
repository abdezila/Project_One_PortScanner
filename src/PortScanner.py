import sys, asyncio, socket

BASIC_PORTS = {
    20: "FTP-DATA", 21: "FTP", 22: "SSH", 23: "TELNET", 25: "SMTP",
    53: "DNS", 80: "HTTP", 110: "POP3", 143: "IMAP", 443: "HTTPS",
    3306: "MySQL", 3389: "RDP", 5432: "PostgreSQL", 6379: "Redis",
    8080: "HTTP-ALT", 27017: "MongoDB",
}

class PortScanner:
    def __init__(self):
        self.host='127.0.0.1'
        self.start_port=1
        self.end_port=1024
        self.threads=100
        self.timeout=2
        self.verbose=False

        self.open_ports=0
        self.closed_ports=0
        self.filtered_ports=0

        self.ip=''

    def set_options(self, host, ports, threads, timeout,verbose):
        self.host=str(host)
        self.threads=int(threads)
        self.timeout=int(timeout)
        self.verbose=bool(verbose)
        self._parser_port(ports)

    def _parser_port(self,port_str:str):
        try:
            if '-' not in port_str:
                end=int(port_str)
                self.start_port=1
                self.end_port=end
            else:
                start_str, end_str = port_str.split('-', 1)
                if int(start_str) > int(end_str) or int(start_str) == 0 or int(end_str) > 65535:
                    self.start_port=1
                    self.end_port=65535
                else:
                    self.start_port=int(start_str)
                    self.end_port=int(end_str)
        except ValueError:
            self.start_port=1
            self.end_port=65535


    async def _resolve(self):
        try:
            loop = asyncio.get_event_loop()
            self.ip = await loop.run_in_executor(None, socket.gethostbyname, self.host)
        except socket.gaierror:
            print(f'[ERROR] Could not get resolve host: {self.host}')
            sys.exit(1)

    async def _scan_port(self, port:int, semaphore:asyncio.Semaphore):
        async with semaphore:
            service = BASIC_PORTS.get(port, '---')
            banner = '---'
            try:
                reader, writer = await asyncio.wait_for(asyncio.open_connection(self.ip, port),
                                                        timeout=self.timeout)
                try:
                    data = await asyncio.wait_for(reader.read(128), timeout=2)
                    if data:
                        banner = data.decode(errors='replace').strip()
                except Exception:
                    pass
                print(f'{port}\tOpen\t{service}\t{banner}')
                self.open_ports+=1
                writer.close()
                await writer.wait_closed()
            except TimeoutError:
                print(f'{port}\tFiltered\t{service}\t{banner}')
                self.filtered_ports+=1
            except (ConnectionRefusedError, OSError):
                print(f'{port}\tClosed\t{service}\t{banner}')
                self.closed_ports+=1

    async def __call__(self):
        await self._resolve()
        semaphore = asyncio.Semaphore(self.threads)
        tasks = [
            self._scan_port(port, semaphore)
            for port in range(self.start_port, self.end_port+1)
        ]
        await asyncio.gather(*tasks)

    def run(self):
        asyncio.run(self.__call__())
        total = self.end_port - self.start_port + 1
        print(f'\n Results for {self.host}:')
        print(f' Open    : {self.open_ports}')
        print(f' Closed  : {self.closed_ports}')
        print(f' Filtered: {self.filtered_ports}')
        print(f' Total scanned : {total}')

