until sudo apt -y update
do
    sleep 1
done
until sudo apt -y install apt-transport-https curl
do
    sleep 1
done

curl -s https://www.webpagetest.org/keys/microsoft/microsoft.asc | sudo apt-key --keyring /etc/apt/trusted.gpg.d/microsoft.gpg add -
echo "deb [arch=amd64] https://packages.microsoft.com/repos/edge stable main" | sudo tee /etc/apt/sources.list.d/microsoft-edge-dev.list

until sudo apt -y update
do
    sleep 1
done
until sudo apt -y install microsoft-edge-dev
do
    sleep 1
done
