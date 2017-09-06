#!/bin/bash
set -e

apt update
apt upgrade -y
apt install -y git vim python3-pip build-essential
# Install Cuda 8 which we'll need for tensorflow
wget http://developer.download.nvidia.com/compute/cuda/repos/ubuntu1604/x86_64/cuda-repo-ubuntu1604_8.0.44-1_amd64.deb
dpkg -i cuda-repo-ubuntu1604_8.0.44-1_amd64.deb
apt-get update
apt-get install -y cuda
# Install cuDNN, which we'll also need for tensorflow
tar -xzvf cudnn-8.0-linux-x64-v6.0.tgz
cp cuda/include/cudnn.h /usr/local/cuda-8.0/include
cp cuda/lib64/libcudnn* /usr/local/cuda-8.0/lib64
chmod a+r /usr/local/cuda-8.0/include/cudnn.h
# Add the path's for cuda library files to .bashrc
echo 'export LD_LIBRARY_PATH=/usr/local/cuda-8.0/lib64:$LD_LIBRARY_PATH' >> \
    /home/ubuntu/.bashrc 
# Install python modules
pip3 install numpy scipy matplotlib pandas jupyter tensorflow-gpu
