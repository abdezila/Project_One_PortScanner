import argparse, asyncio
from src.PortScanner import PortScanner

parser = argparse.ArgumentParser(description='Port Scanner project',
                                 epilog='''Example
                                        python PortScanner -i 127.0.0.1 -p 80-235
                                 ''')

parser.add_argument('-i', '--ip', default='127.0.0.1', help='Target ip')
parser.add_argument('-p', '--ports', default='1-1024', help='Port range')
parser.add_argument('-t', '--threads', type=int, default=100, help='Max Concurrency')
parser.add_argument('-e', '--timeout', type=int, default=2, help='Time Out Filtered')
parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
args = parser.parse_args()

async def main():
    scanner = PortScanner()
    scanner.set_options(
        host =args.host,
        ports=args.ports,
        threads=args.threads,
        timeout=args.timeout,
        verbose=args.verbose,
    )
    await scanner()

if __name__ == '__main__':
    asyncio.run(main())