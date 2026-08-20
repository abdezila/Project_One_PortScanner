import sys, asyncio, socket

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

    def _parser_port(port_str:str):
        pass

    def _resolve(sefl):
        pass

    def _scan(self):
        pass

    def __call__(self):
        pass

    def run(self):
        pass
