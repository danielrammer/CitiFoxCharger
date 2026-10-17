"""Configure optional tablet LAN access locally; never accepts a password as an argument."""
import argparse
import base64
import csv
import getpass
import hashlib
import hmac
import io
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from citifoxcharger.profile import default_data_dir
from citifoxcharger.config import atomic_write
from citifoxcharger.lifecycle import LocalControl,ControlError
from citifoxcharger.network import password_record,private_host,read_network,tls_context
from citifoxcharger.secrets_store import dpapi


def powershell(script,data):
    if os.name!='nt':raise ValueError('Network setup requires Windows and .NET Framework 4.7.2 or newer.')
    binary=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    result=subprocess.run([str(binary),'-NoProfile','-NonInteractive','-Command',script],input=json.dumps(data),capture_output=True,text=True,encoding='utf-8',timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:raise ValueError('Windows could not complete network setup. Check Windows/.NET and permissions.')
    return result.stdout


def pem(label,data):
    text=base64.b64encode(data).decode('ascii')
    return (f'-----BEGIN {label}-----\n'+'\n'.join(text[i:i+64] for i in range(0,len(text),64))+f'\n-----END {label}-----\n').encode('ascii')


def der(tag,data):
    length=len(data)
    encoded=bytes([length]) if length<128 else length.to_bytes((length.bit_length()+7)//8,'big')
    if length>=128:encoded=bytes([128+len(encoded)])+encoded
    return bytes([tag])+encoded+data


def private_pem(numbers):
    # PKCS#1 encoding only; all key generation/signing is performed by Windows.
    values=[der(2,b'\0')]
    for name in ('Modulus','Exponent','D','P','Q','DP','DQ','InverseQ'):
        value=base64.b64decode(numbers[name],validate=True).lstrip(b'\0') or b'\0'
        if value[0]&128:value=b'\0'+value
        values.append(der(2,value))
    return pem('RSA PRIVATE KEY',der(48,b''.join(values)))


def create_certificate(directory,host):
    directory=Path(directory)/'network'
    directory.mkdir(parents=True,exist_ok=True)
    identity=subprocess.check_output(['whoami','/user','/fo','csv','/nh'],text=True,errors='replace')
    sid=list(csv.reader(io.StringIO(identity)))[0][-1]
    if not sid.startswith('S-1-5-'):raise ValueError('The Windows user could not be identified.')
    result=subprocess.run(['icacls',str(directory),'/inheritance:r','/grant:r',f'*{sid}:(OI)(CI)F','*S-1-5-18:(OI)(CI)F'],capture_output=True)
    if result.returncode:raise ValueError('Protected certificate storage could not be configured.')
    script=(Path(__file__).parent/'citifoxcharger/network_certificate.ps1').read_text(encoding='utf-8')
    value=json.loads(powershell(script,{'host':host}))
    root=base64.b64decode(value['root'],validate=True)
    atomic_write(directory/'CitiFoxCharger-Home-Network.cer',root)
    atomic_write(directory/'server.pem',pem('CERTIFICATE',base64.b64decode(value['certificate'],validate=True))+pem('CERTIFICATE',root))
    atomic_write(directory/'server-key.dpapi',dpapi(private_pem(value['key'])))
    tls_context(directory.parent)  # Check matching certificate/key before enabling anything.
    return value['expires'],hashlib.sha256(root).hexdigest()


def suggested_host(directory):
    try:
        host=json.loads((Path(directory)/'config.json').read_text(encoding='utf-8'))['smartfox']['host']
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as client:
            client.connect((host,9))  # Route selection only; no datagram is sent.
            return private_host(client.getsockname()[0])
    except (OSError,ValueError,KeyError,TypeError):return ''


def main():
    parser=argparse.ArgumentParser(description='Configure CitiFoxCharger home network access')
    parser.add_argument('--data-dir',type=Path,default=default_data_dir())
    parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--backend-port',type=int,default=8765)
    parser.add_argument('--disable',action='store_true')
    parser.add_argument('--firewall',action='store_true')
    args=parser.parse_args()
    try:
        if args.firewall:
            import ctypes
            if not ctypes.windll.shell32.IsUserAnAdmin():raise ValueError('Right-click Setup-Firewall.cmd and choose Run as administrator.')
            config=read_network(args.data_dir)
            if not config:raise ValueError('First run Setup-Network.cmd under the usual Windows user.')
            script=(Path(__file__).parent/'citifoxcharger/network_firewall.ps1').read_text(encoding='utf-8')
            powershell(script,{'host':config['host'],'port':config['port'],'program':sys.executable})
            print('Firewall access configured: private network and local subnet only.')
            return 0
        control=LocalControl(args.backend_port)
        health=control.health()
        if health:
            control.request('/api/session')
            access=control.request('/api/config')['key']
            if access['configured'] and not access['persisted']:raise ValueError('First save the MyŠkoda key with Windows encryption in Settings.')
        path=args.data_dir/'network.json'
        if args.disable:
            if path.exists():
                value=json.loads(path.read_text(encoding='utf-8'));value['enabled']=False
                atomic_write(path,json.dumps(value).encode('utf-8'))
        else:
            if not 1024<=args.port<=65535 or args.port==args.backend_port:raise ValueError('Network port must differ from the local backend port (1024 to 65535).')
            default=suggested_host(args.data_dir)
            host=private_host(input(f'IPv4 address of this tablet [{default}]: ').strip() or default)
            print('Leave the password empty for home network access without a password.')
            print('Without a password, every device on the reachable home network can operate charging and heaters.')
            password=getpass.getpass('Network password (12+ characters, or Enter for none): ')
            if password:
                record={**password_record(password),'authentication':'password'}
                if not hmac.compare_digest(password.encode('utf-8'),getpass.getpass('Repeat network password: ').encode('utf-8')):raise ValueError('The passwords do not match.')
            else:
                record={'authentication':'none','salt':None,'password_hash':None,'iterations':0}
            del password
            expiry,fingerprint=create_certificate(args.data_dir,host)
            value={'enabled':True,'host':host,'port':args.port,**record,'certificate_expires':expiry}
            atomic_write(path,json.dumps(value,indent=2).encode('utf-8'))
            certificate=args.data_dir/'network/CitiFoxCharger-Home-Network.cer'
            try:shutil.copy2(certificate,Path(__file__).parent/certificate.name)
            except OSError:pass
            print(f'Home network URL: https://{host}:{args.port}/')
            print('Username: citifox' if record['authentication']=='password' else 'Password: none. All reachable home network devices can operate this dashboard.')
            print(f'Public certificate for other devices: {certificate}\nSHA256: {fingerprint}')
            print('Trust the certificate on client devices. See NETWORK.md for access instructions.')
        print(control.action('restart',args.data_dir,args.backend_port,no_browser=True))
        control.request('/api/session')
        network=control.request('/api/config')['network']
        if bool(network['enabled']) != (not args.disable):
            raise ControlError(network['message'])
        if not args.disable:
            print('Run Setup-Firewall.cmd as administrator once on this tablet. Windows must classify the network as Private.')
        return 0
    except (ValueError,ControlError) as error:
        print(str(error))
        return 1
    except (OSError,subprocess.SubprocessError):
        # Do not print a captured PowerShell exception/stdout: it may contain private material.
        print('Setup is unconfirmed. Check Windows/.NET, the private tablet IP and saved MyŠkoda access. Run firewall setup as administrator. See NETWORK.md for secure setup details.')
        return 1


if __name__=='__main__':raise SystemExit(main())
