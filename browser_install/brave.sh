until sudo apt -y update
do
    sleep 1
done
until sudo apt -y install apt-transport-https curl
do
    sleep 1
done

curl -s https://www.webpagetest.org/keys/brave/release.asc | sudo apt-key --keyring /etc/apt/trusted.gpg.d/brave-browser-release.gpg add -
echo "deb [arch=amd64] https://brave-browser-apt-release.s3.brave.com/ stable main" | sudo tee /etc/apt/sources.list.d/brave-browser-release.list

until sudo apt -y update
do
    sleep 1
done
until sudo apt -y install brave-browser
do
    sleep 1
done
