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
curl -s https://www.webpagetest.org/keys/brave/beta.asc | sudo apt-key --keyring /etc/apt/trusted.gpg.d/brave-browser-prerelease.gpg add -
echo "deb [arch=amd64] https://brave-browser-apt-beta.s3.brave.com/ stable main" | sudo tee /etc/apt/sources.list.d/brave-browser-beta.list
curl -s https://www.webpagetest.org/keys/brave/dev.asc | sudo apt-key --keyring /etc/apt/trusted.gpg.d/brave-browser-prerelease.gpg add -
echo "deb [arch=amd64] https://brave-browser-apt-dev.s3.brave.com/ stable main" | sudo tee /etc/apt/sources.list.d/brave-browser-dev.list
curl -s https://www.webpagetest.org/keys/brave/nightly.asc | sudo apt-key --keyring /etc/apt/trusted.gpg.d/brave-browser-prerelease.gpg add -
echo "deb [arch=amd64] https://brave-browser-apt-nightly.s3.brave.com/ stable main" | sudo tee /etc/apt/sources.list.d/brave-browser-nightly.list


until sudo apt -y update
do
    sleep 1
done
until sudo apt -y install brave-browser brave-browser-beta brave-browser-dev brave-browser-nightly
do
    sleep 1
done
