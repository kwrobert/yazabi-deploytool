#!/bin/bash
set -e

# This just supresses warnings from apt when it tires to access stdin
export DEBIAN_FRONTEND=noninteractive

echo "############## Upgrading and Installing standard packages ###############"
apt update
apt upgrade -y
apt install -y git vim python3-pip build-essential libopenblas-dev \
               liblapack-dev python-dev
# Install Cuda 8 which we'll need for tensorflow
echo "############## Retrieving and Installing CUDA ###############"
wget http://developer.download.nvidia.com/compute/cuda/repos/ubuntu1604/x86_64/cuda-repo-ubuntu1604_8.0.44-1_amd64.deb
dpkg -i cuda-repo-ubuntu1604_8.0.44-1_amd64.deb
apt-get update
apt-get install -y cuda
echo "############## CUDA Installion Complete! ###############"
# Install cuDNN, which we'll also need for tensorflow
echo "############## Unpacking and Installing cuDNN ###############"
tar -xzf cudnn-8.0-linux-x64-v6.0.tgz
cp cuda/include/cudnn.h /usr/local/cuda-8.0/include
cp cuda/lib64/libcudnn* /usr/local/cuda-8.0/lib64
chmod a+r /usr/local/cuda-8.0/include/cudnn.h
echo "############## cuDNN Installation Complete! ###############"
# Add the path's for cuda library files to .bashrc
export LD_LIBRARY_PATH=/usr/local/cuda-8.0/lib64:$LD_LIBRARY_PATH
echo 'export LD_LIBRARY_PATH=/usr/local/cuda-8.0/lib64:$LD_LIBRARY_PATH' >> \
    /home/ubuntu/.bashrc 
# Need to add cuda binaries to the path if we ever want to install theano or
# pycuda or other such things
echo 'export PATH=/usr/local/cuda-8.0/bin:$PATH' >> /home/ubuntu/.bashrc 
# For root as well
export CUDA_ROOT=/usr/local/cuda-8.0
# Install python modules
echo "############## Installing Python Modules ###############"
pip3 install numpy scipy matplotlib pandas jupyter tensorflow-gpu scikit-learn keras
echo "############## Python Modules Installed! ###############"
# Need to reboot to load nvidia drivers
echo "############## Rebooting now! ###############"
reboot now
