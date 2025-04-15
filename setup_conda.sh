# (optional) setup conda environment
mkdir -p ~/miniconda3
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O ~/miniconda3/miniconda.sh
bash ~/miniconda3/miniconda.sh -b -u -p ~/miniconda3
rm ~/miniconda3/miniconda.sh

source ~/miniconda3/bin/activate
conda init --all 

conda create -y -n agent-env python=3.11
conda activate agent-env
pip install -r computer-use-demo/computer_use_demo/requirements.txt
pip install -r /workspace/PC-Canary/requirements.txt