#!/bin/bash

# =============================================================================
# Any Auto Register - 一键部署脚本
# =============================================================================
# 此脚本将自动完成以下操作:
# 1. 检查并创建 Conda 环境
# 2. 安装 Python 依赖
# 3. 安装浏览器 (Playwright + Camoufox)
# 4. 安装前端依赖并构建
# 5. 启动服务
# =============================================================================

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 配置
APP_NAME="any-auto-register"
CONDA_ENV_NAME="${APP_NAME}"
PYTHON_VERSION="3.12"
PORT="8000"

# 打印带颜色的消息
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查命令是否存在
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# 检查是否在正确的目录
check_directory() {
    if [ ! -f "main.py" ] || [ ! -f "requirements.txt" ]; then
        print_error "请在项目根目录下运行此脚本"
        exit 1
    fi
}

# 检查 Conda 是否安装
check_conda() {
    if ! command_exists conda; then
        print_error "未检测到 Conda，请先安装 Conda"
        print_info "下载地址：https://docs.conda.io/en/latest/miniconda.html"
        exit 1
    fi
    print_success "Conda 已安装"
}

# 创建或激活 Conda 环境
setup_conda_env() {
    print_info "检查 Conda 环境: ${CONDA_ENV_NAME}"
    
    if conda env list | grep -q "^${CONDA_ENV_NAME} "; then
        print_success "Conda 环境已存在"
    else
        print_info "创建 Conda 环境: ${CONDA_ENV_NAME} (Python ${PYTHON_VERSION})"
        conda create -n "${CONDA_ENV_NAME}" python="${PYTHON_VERSION}" -y
        print_success "Conda 环境创建完成"
    fi
    
    print_info "激活 Conda 环境"
    source "$(conda info --base)/etc/profile.d/conda.sh"
    conda activate "${CONDA_ENV_NAME}"
    print_success "Conda 环境已激活"
}

# 安装 Python 依赖
install_python_deps() {
    print_info "安装 Python 依赖..."
    pip install -r requirements.txt -q
    print_success "Python 依赖安装完成"
}

# 安装浏览器
install_browsers() {
    print_info "安装 Playwright 浏览器..."
    python -m playwright install chromium
    print_success "Playwright 浏览器安装完成"
    
    print_info "获取 Camoufox 浏览器..."
    python -m camoufox fetch
    print_success "Camoufox 浏览器获取完成"
}

# 安装前端依赖并构建
setup_frontend() {
    if [ ! -d "frontend" ]; then
        print_warning "未找到 frontend 目录，跳过前端构建"
        return
    fi
    
    if ! command_exists npm; then
        print_warning "未检测到 Node.js，跳过前端构建"
        print_info "如需前端界面，请安装 Node.js 18+"
        return
    fi
    
    print_info "安装前端依赖..."
    cd frontend
    npm install --silent
    print_success "前端依赖安装完成"
    
    print_info "构建前端..."
    npm run build --silent
    cd ..
    print_success "前端构建完成"
}

# 创建环境变量文件
setup_env_file() {
    if [ ! -f ".env" ]; then
        if [ -f ".env.example" ]; then
            print_info "创建环境变量文件..."
            cp .env.example .env
            print_warning "请编辑 .env 文件配置您的参数"
        else
            print_info "创建默认环境变量文件..."
            cat > .env << 'EOF'
# Any Auto Register - 环境变量配置
HOST=0.0.0.0
PORT=8000
APP_RELOAD=0
APP_CONDA_ENV=any-auto-register

# 验证码服务
YESCAPTCHA_CLIENT_KEY=
LOCAL_SOLVER_URL=http://127.0.0.1:8889

# 代理配置 (可选)
PROXY_URL=

# 邮箱服务配置 (根据需要使用)
MOEMAIL_API_KEY=
SKYMAIL_API_KEY=
SKYMAIL_DOMAIN=
EOF
            print_success "默认环境变量文件创建完成"
            print_warning "请编辑 .env 文件配置您的参数"
        fi
    else
        print_success "环境变量文件已存在"
    fi
}

# 启动服务
start_service() {
    print_info "启动服务..."
    print_info "访问地址：http://localhost:${PORT}"
    print_info "API 文档：http://localhost:${PORT}/docs"
    print_info ""
    print_info "按 Ctrl+C 停止服务"
    print_info ""
    
    # 后台启动
    nohup python main.py > backend.log 2>&1 &
    echo $! > backend.pid
    
    sleep 3
    
    if ps -p $(cat backend.pid) > /dev/null 2>&1; then
        print_success "服务启动成功!"
        print_info "日志文件：backend.log"
        print_info "进程 PID: $(cat backend.pid)"
    else
        print_error "服务启动失败，请查看 backend.log"
        exit 1
    fi
}

# 主函数
main() {
    echo ""
    echo "=============================================="
    echo "  Any Auto Register - 一键部署脚本"
    echo "=============================================="
    echo ""
    
    check_directory
    check_conda
    setup_conda_env
    install_python_deps
    install_browsers
    setup_frontend
    setup_env_file
    
    echo ""
    echo "=============================================="
    echo "  部署完成!"
    echo "=============================================="
    echo ""
    
    # 询问是否启动服务
    read -p "是否立即启动服务？(y/n) " -n 1 -r
    echo ""
    
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        start_service
    else
        print_info "您可以稍后手动启动服务:"
        echo "  conda activate ${CONDA_ENV_NAME}"
        echo "  python main.py"
    fi
    
    echo ""
    print_success "部署完成!"
}

# 运行主函数
main "$@"
