#!/bin/bash

# =============================================================================
# Any Auto Register - 快速更新脚本
# =============================================================================
# 此脚本将自动完成以下操作:
# 1. 停止当前运行的服务
# 2. 拉取最新代码
# 3. 更新 Python 依赖
# 4. 更新前端依赖并重新构建
# 5. 重新启动服务
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

# 停止当前服务
stop_service() {
    print_info "检查正在运行的服务..."
    
    if [ -f "backend.pid" ]; then
        PID=$(cat backend.pid)
        if ps -p "$PID" > /dev/null 2>&1; then
            print_info "停止服务 (PID: $PID)..."
            kill "$PID" 2>/dev/null || true
            sleep 2
            
            # 如果进程还在，强制停止
            if ps -p "$PID" > /dev/null 2>&1; then
                print_warning "进程仍在运行，强制停止..."
                kill -9 "$PID" 2>/dev/null || true
            fi
            
            rm -f backend.pid
            print_success "服务已停止"
        else
            print_info "PID 文件存在但进程未运行"
            rm -f backend.pid
        fi
    else
        # 尝试通过进程名停止
        if pgrep -f "python main.py" > /dev/null 2>&1; then
            print_info "停止服务..."
            pkill -f "python main.py" || true
            sleep 2
            print_success "服务已停止"
        else
            print_info "没有正在运行的服务"
        fi
    fi
}

# 激活 Conda 环境
activate_conda_env() {
    print_info "激活 Conda 环境: ${CONDA_ENV_NAME}"
    
    if ! command_exists conda; then
        print_error "未检测到 Conda"
        exit 1
    fi
    
    source "$(conda info --base)/etc/profile.d/conda.sh"
    conda activate "${CONDA_ENV_NAME}"
    print_success "Conda 环境已激活"
}

# 拉取最新代码
pull_latest_code() {
    print_info "拉取最新代码..."
    
    if [ -d ".git" ]; then
        git fetch origin
        git reset --hard origin/main
        print_success "代码已更新到最新版本"
    else
        print_warning "未检测到 Git 仓库，跳过代码更新"
    fi
}

# 更新 Python 依赖
update_python_deps() {
    print_info "更新 Python 依赖..."
    pip install -r requirements.txt -q --upgrade
    print_success "Python 依赖更新完成"
}

# 更新浏览器
update_browsers() {
    print_info "检查浏览器更新..."
    
    # 更新 Playwright 浏览器
    python -m playwright install chromium 2>/dev/null || true
    
    # 更新 Camoufox
    python -m camoufox fetch 2>/dev/null || true
    
    print_success "浏览器检查完成"
}

# 更新前端
update_frontend() {
    if [ ! -d "frontend" ]; then
        print_warning "未找到 frontend 目录，跳过前端更新"
        return
    fi
    
    if ! command_exists npm; then
        print_warning "未检测到 Node.js，跳过前端更新"
        return
    fi
    
    print_info "更新前端依赖..."
    cd frontend
    npm install --silent
    print_success "前端依赖更新完成"
    
    print_info "重新构建前端..."
    npm run build --silent
    cd ..
    print_success "前端构建完成"
}

# 启动服务
start_service() {
    print_info "启动服务..."
    
    # 后台启动
    nohup python main.py > backend.log 2>&1 &
    echo $! > backend.pid
    
    sleep 3
    
    if ps -p $(cat backend.pid) > /dev/null 2>&1; then
        print_success "服务启动成功!"
        print_info "日志文件：backend.log"
        print_info "进程 PID: $(cat backend.pid)"
        print_info "访问地址：http://localhost:${PORT}"
        print_info "API 文档：http://localhost:${PORT}/docs"
    else
        print_error "服务启动失败，请查看 backend.log"
        exit 1
    fi
}

# 显示更新日志
show_changelog() {
    echo ""
    print_info "最近提交记录:"
    echo ""
    git log --oneline -5 2>/dev/null || true
    echo ""
}

# 主函数
main() {
    echo ""
    echo "=============================================="
    echo "  Any Auto Register - 快速更新脚本"
    echo "=============================================="
    echo ""
    
    check_directory
    stop_service
    activate_conda_env
    pull_latest_code
    show_changelog
    update_python_deps
    update_browsers
    update_frontend
    
    echo ""
    echo "=============================================="
    echo "  更新完成!"
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
    print_success "更新完成!"
}

# 运行主函数
main "$@"
