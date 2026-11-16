#!/usr/bin/env bash
# Kiểm tra máy chủ WSL2 đã sẵn sàng cho deploy production chưa.
# Chạy dưới user `deploy`, KHÔNG cần sudo, KHÔNG đọc nội dung file bí mật.
#
#   scripts/windows/check_host.sh             # kiểm tra nhanh
#   scripts/windows/check_host.sh --gpu-test  # thêm: chạy container CUDA thử nvidia-smi (kéo image ~100 MB)
#
# Mã thoát: 0 = không có FAIL (có thể có WARN), 1 = có ít nhất một FAIL.
# Xem hướng dẫn sửa từng mục trong docs/deploy/SETUP_WINDOWS.md.

set -u
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/lib/wsl/lib"

GPU_TEST=0
for a in "$@"; do
  case "$a" in
    --gpu-test) GPU_TEST=1 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "Tham số không hợp lệ: $a" >&2; exit 2 ;;
  esac
done

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
CUDA_TEST_IMAGE="${CUDA_TEST_IMAGE:-nvidia/cuda:12.8.1-base-ubuntu24.04}"
MIN_MEM_GB=22     # .wslconfig memory=24GB → free -g báo ~23
MIN_SWAP_GB=7     # swap=8GB
MIN_DISK_GB=100

N_PASS=0; N_WARN=0; N_FAIL=0
if [ -t 1 ]; then G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; B=$'\e[1m'; Z=$'\e[0m'; else G=; Y=; R=; B=; Z=; fi
pass() { N_PASS=$((N_PASS+1)); printf '  %s[PASS]%s %s\n' "$G" "$Z" "$*"; }
warn() { N_WARN=$((N_WARN+1)); printf '  %s[WARN]%s %s\n' "$Y" "$Z" "$*"; }
fail() { N_FAIL=$((N_FAIL+1)); printf '  %s[FAIL]%s %s\n' "$R" "$Z" "$*"; }
info() { printf '  [INFO] %s\n' "$*"; }
section() { printf '\n%s== %s ==%s\n' "$B" "$*" "$Z"; }
have() { command -v "$1" >/dev/null 2>&1; }

echo "check_host.sh — $(date '+%Y-%m-%d %H:%M:%S %Z') — user=$(id -un) host=$(hostname)"

# ---------------------------------------------------------------- WSL / OS
section "WSL / hệ điều hành"
if grep -qi microsoft /proc/version 2>/dev/null; then pass "Đang chạy trong WSL2 ($(uname -r))"
else warn "Không phát hiện WSL (/proc/version) — script viết cho WSL2"; fi

. /etc/os-release 2>/dev/null || true
if [ "${VERSION_ID:-}" = "24.04" ]; then pass "Ubuntu ${VERSION_ID}"
else fail "Cần Ubuntu 24.04, đang là ${PRETTY_NAME:-không rõ}"; fi

if grep -Eq '^[[:space:]]*systemd[[:space:]]*=[[:space:]]*true' /etc/wsl.conf 2>/dev/null; then pass "/etc/wsl.conf có systemd=true"
else fail "/etc/wsl.conf thiếu [boot] systemd=true"; fi

st=$(systemctl is-system-running 2>/dev/null || true)
case "$st" in
  running) pass "systemd: running" ;;
  degraded) warn "systemd: degraded — xem: systemctl --failed" ;;
  *) fail "systemd không chạy (trạng thái: ${st:-không rõ})" ;;
esac

if [ "$(id -un)" = "deploy" ] && [ "$(id -u)" = "1000" ]; then pass "User deploy (uid 1000)"
else warn "Nên chạy bằng user deploy (uid 1000); đang là $(id -un) uid $(id -u)"; fi

tz=$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || echo "?")
if [ "$tz" = "Asia/Ho_Chi_Minh" ]; then pass "Múi giờ $tz"
else warn "Múi giờ là $tz (khuyến nghị Asia/Ho_Chi_Minh để log/backup dễ đọc)"; fi

# ---------------------------------------------------------------- Tài nguyên
section "Tài nguyên (phản ánh %UserProfile%\\.wslconfig)"
mem_kb=$(awk '/^MemTotal:/{print $2}' /proc/meminfo); mem_gb=$((mem_kb/1024/1024))
swap_kb=$(awk '/^SwapTotal:/{print $2}' /proc/meminfo); swap_gb=$((swap_kb/1024/1024))
if [ "$mem_gb" -ge "$MIN_MEM_GB" ]; then pass "RAM WSL ${mem_gb} GB"
else fail "RAM WSL ${mem_gb} GB < ${MIN_MEM_GB} GB — tạo .wslconfig memory=24GB rồi 'wsl --shutdown'"; fi
if [ "$swap_gb" -ge "$MIN_SWAP_GB" ]; then pass "Swap ${swap_gb} GB"
else fail "Swap ${swap_gb} GB < ${MIN_SWAP_GB} GB — .wslconfig swap=8GB"; fi
info "CPU: $(nproc) nhân"
disk_gb=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
if [ "${disk_gb:-0}" -ge "$MIN_DISK_GB" ]; then pass "Đĩa / còn trống ${disk_gb} GB"
else fail "Đĩa / chỉ còn ${disk_gb} GB (< ${MIN_DISK_GB} GB)"; fi

# .wslconfig phía Windows (đọc qua /mnt/c nếu interop/automount có)
CMD=/mnt/c/Windows/System32/cmd.exe
if [ -x "$CMD" ]; then
  winprof=$(cd /mnt/c 2>/dev/null && "$CMD" /c 'echo %USERPROFILE%' 2>/dev/null | tr -d '\r')
  if [ -n "$winprof" ] && have wslpath; then
    wslcfg="$(wslpath "$winprof")/.wslconfig"
    if [ -f "$wslcfg" ]; then
      pass ".wslconfig tồn tại: $winprof\\.wslconfig"
      grep -Eiq '^[[:space:]]*vmIdleTimeout[[:space:]]*=[[:space:]]*-1' "$wslcfg" \
        && pass ".wslconfig: vmIdleTimeout=-1" \
        || warn ".wslconfig thiếu vmIdleTimeout=-1 (VM có thể tự tắt khi không ai mở terminal)"
    else
      fail "Chưa có $winprof\\.wslconfig"
    fi
  else
    warn "Không lấy được %USERPROFILE% qua cmd.exe — tự kiểm .wslconfig bên Windows"
  fi
else
  warn "Không thấy $CMD (interop tắt?) — tự kiểm .wslconfig bên Windows"
fi

# ---------------------------------------------------------------- GPU
section "GPU (driver Windows, dùng chung qua /usr/lib/wsl/lib)"
if [ -x /usr/lib/wsl/lib/nvidia-smi ]; then
  if gpu=$(/usr/lib/wsl/lib/nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used --format=csv,noheader 2>/dev/null); then
    pass "nvidia-smi: $gpu"
  else
    fail "/usr/lib/wsl/lib/nvidia-smi lỗi — kiểm driver NVIDIA bên Windows"
  fi
else
  fail "Không có /usr/lib/wsl/lib/nvidia-smi — cài/cập nhật driver NVIDIA bên Windows"
fi
if ls /usr/lib/x86_64-linux-gnu/libcuda.so* >/dev/null 2>&1 && dpkg -S /usr/lib/x86_64-linux-gnu/libcuda.so.1 >/dev/null 2>&1; then
  fail "Có libcuda của driver Linux trong distro — KHÔNG cài driver NVIDIA Linux trong WSL (gỡ gói nvidia-driver-*/libnvidia-compute-*)"
else
  pass "Không có driver NVIDIA Linux cài nhầm trong distro"
fi

# ---------------------------------------------------------------- Docker
section "Docker Engine (trong WSL, không phải Docker Desktop)"
if ! have docker; then
  fail "Chưa có lệnh docker — cài Docker Engine (SETUP_WINDOWS.md, phần B2)"
else
  dpath=$(readlink -f "$(command -v docker)")
  case "$dpath" in
    /mnt/*) fail "docker trỏ sang Docker Desktop ($dpath) — tắt WSL integration của Desktop" ;;
    *) pass "docker CLI: $dpath" ;;
  esac
  for svc in docker.service containerd.service; do
    a=$(systemctl is-active "$svc" 2>/dev/null || true); e=$(systemctl is-enabled "$svc" 2>/dev/null || true)
    if [ "$a" = "active" ] && [ "$e" = "enabled" ]; then pass "$svc: active, enabled"
    else fail "$svc: active=$a enabled=$e — cần 'systemctl enable --now $svc'"; fi
  done
  if id -nG | tr ' ' '\n' | grep -qx docker; then pass "User $(id -un) thuộc group docker"
  else fail "User $(id -un) chưa thuộc group docker"; fi

  if dinfo=$(docker info --format '{{json .}}' 2>/dev/null); then
    sv=$(docker version --format '{{.Server.Version}}' 2>/dev/null)
    pass "Docker daemon trả lời (server $sv)"
    ctx=$(docker context show 2>/dev/null || true)
    [ "$ctx" = "default" ] && pass "docker context: default" || warn "docker context: $ctx (nên là default)"
    case "$dinfo" in *'"OperatingSystem":"Docker Desktop"'*) fail "Daemon là Docker Desktop, không phải Engine" ;; esac
    if printf '%s' "$dinfo" | grep -q '"nvidia"'; then pass "Docker có runtime nvidia (NVIDIA Container Toolkit)"
    else fail "Docker chưa có runtime nvidia — cài nvidia-container-toolkit + 'nvidia-ctk runtime configure --runtime=docker'"; fi
    ld=$(docker info --format '{{.LoggingDriver}}' 2>/dev/null)
    if [ -r /etc/docker/daemon.json ] && grep -q '"max-size"' /etc/docker/daemon.json; then pass "Log driver $ld có giới hạn kích thước (daemon.json)"
    else warn "Chưa giới hạn log container (daemon.json log-opts max-size) — log có thể đầy đĩa"; fi
    droot=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null)
    case "$droot" in /mnt/*) fail "DockerRootDir nằm trên ổ Windows ($droot)" ;; *) pass "DockerRootDir: $droot" ;; esac
  else
    fail "Không kết nối được Docker daemon (đã đăng xuất/đăng nhập lại sau khi thêm group docker?)"
  fi
  if cv=$(docker compose version --short 2>/dev/null); then
    cmajor=$(printf '%s' "$cv" | sed -E 's/^v//; s/[^0-9].*$//')
    if [ -n "$cmajor" ] && [ "$cmajor" -ge 2 ]; then pass "docker compose plugin $cv"
    else warn "docker compose $cv (cần >= 2)"; fi
  else fail "Thiếu docker compose plugin (gói docker-compose-plugin)"; fi
  have docker-buildx || docker buildx version >/dev/null 2>&1 && pass "docker buildx có sẵn" || warn "Thiếu docker buildx (gói docker-buildx-plugin)"

  if [ "$GPU_TEST" = 1 ]; then
    info "Chạy thử GPU trong container: $CUDA_TEST_IMAGE"
    if out=$(docker run --rm --gpus all "$CUDA_TEST_IMAGE" nvidia-smi --query-gpu=name --format=csv,noheader 2>&1); then
      pass "GPU trong container: $(printf '%s' "$out" | tail -1)"
    else
      fail "docker run --gpus all lỗi: $(printf '%s' "$out" | tail -2 | tr '\n' ' ')"
    fi
  else
    info "Bỏ qua chạy thử GPU trong container (thêm --gpu-test)"
  fi
fi

# ---------------------------------------------------------------- Docker Desktop
section "Docker Desktop phải tắt"
if [ -S /mnt/wsl/docker-desktop/shared-sockets/guest-services/docker.proxy.sock ] || [ -d /mnt/wsl/docker-desktop-bind-mounts ]; then
  warn "Còn dấu vết Docker Desktop đang chạy (/mnt/wsl/docker-desktop*) — Quit Docker Desktop, tắt 'Start when you sign in'"
else
  pass "Không thấy Docker Desktop đang chạy"
fi

# ---------------------------------------------------------------- Công cụ
section "Công cụ dòng lệnh"
for t in git curl flock jq /usr/bin/python3 sha256sum gzip; do
  if have "$t"; then pass "$t"; else
    case "$t" in jq) fail "Thiếu jq (apt install jq) — deploy.sh dùng để đọc GitHub API" ;; *) fail "Thiếu $t" ;; esac
  fi
done

# ---------------------------------------------------------------- Repo & bí mật
section "Repo và file bí mật (chỉ kiểm quyền, không đọc nội dung)"
case "$REPO_DIR" in /mnt/*|/root/*) fail "Repo nằm ở $REPO_DIR — phải ở /home/deploy" ;; *) pass "Repo: $REPO_DIR" ;; esac
if git -C "$REPO_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  info "Branch: $(git -C "$REPO_DIR" branch --show-current), HEAD $(git -C "$REPO_DIR" rev-parse --short HEAD)"
  url=$(git -C "$REPO_DIR" remote get-url origin 2>/dev/null || true)
  [ -n "$url" ] && pass "Remote origin có cấu hình" || warn "Chưa có remote origin (deploy.sh cần fetch origin/main)"
fi
for f in .env .env.prod frontend/.env.local firebase-credentials.json; do
  p="$REPO_DIR/$f"
  if [ ! -e "$p" ]; then warn "Không có $f"; continue; fi
  mode=$(stat -c '%a' "$p"); owner=$(stat -c '%U' "$p")
  if [ "$mode" = "600" ] && [ "$owner" = "deploy" ]; then pass "$f: 600, chủ deploy"
  else fail "$f: quyền $mode, chủ $owner (cần 600, deploy)"; fi
  if git -C "$REPO_DIR" ls-files --error-unmatch "$f" >/dev/null 2>&1; then fail "$f đang bị git theo dõi!"
  elif git -C "$REPO_DIR" check-ignore -q "$f"; then pass "$f được gitignore"
  else fail "$f KHÔNG được gitignore"; fi
done
if [ -f /home/deploy/data/law.sql ] || [ -f /home/deploy/data/law.sql.gz ]; then pass "Có dump MySQL trong /home/deploy/data"
else info "Chưa copy dump MySQL sang /home/deploy/data (cần ở bước MIGRATE_DATA)"; fi

# ---------------------------------------------------------------- Tổng kết
printf '\n%sTổng kết:%s %s%d PASS%s, %s%d WARN%s, %s%d FAIL%s\n' "$B" "$Z" "$G" "$N_PASS" "$Z" "$Y" "$N_WARN" "$Z" "$R" "$N_FAIL" "$Z"
[ "$N_FAIL" -eq 0 ]
