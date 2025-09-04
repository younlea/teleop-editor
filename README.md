# Teleoperation Recorder & Editor

## Install to nvidia jetson orin 
### install uv 

```bash
pip install uv
```

#### Node.js + npm

```bash
# 1. Remove old system Node.js
sudo apt purge -y nodejs nodejs-doc libnode72
sudo apt autoremove -y

# 2. Install NVM (Node Version Manager)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
source ~/.bashrc

# 3. Install Node.js 20 LTS
nvm install 20
nvm use 20

# 4. Verify
node -v   # should show v20.x
npm -v    # should show 10.x
```

## Update 할때

### Backend

```bash
cd backend
uv sync
```

### Fronted

```bash
cd frontend
npm install
npm run build
```


## 실행

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

```bash
cd frontend
node .output/server/index.mjs
```
