pip install dnspython monotonic pillow psutil pypiwin32 requests ujson tornado marionette_driver selenium future
cd $env:USERPROFILE
git clone https://github.com/WPO-Foundation/browser-install.git .\browser-install
git clone https://github.com/WPO-Foundation/wptagent.git .\wptagent
python .\browser-install\browser-install.py --all -vvvv
schtasks /delete /tn WPTPostReboot /f
