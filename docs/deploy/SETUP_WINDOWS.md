# Chuẩn bị máy chủ Windows 11 + WSL2 (chủ máy tự làm)

Các bước dưới đây cần **quyền Administrator Windows** hoặc **root trong WSL** — user `deploy`
(nơi Claude Code chạy) không có sudo nên không tự làm được. Làm theo thứ tự, xong chạy
`scripts/windows/check_host.sh --gpu-test` dưới user `deploy` để kiểm.

Bối cảnh và lý do các quyết định: `docs/deploy/SURVEY.md`.

Trạng thái lúc viết (16/11/2026, `check_host.sh`): WSL 15 GB RAM / 4 GB swap, chưa có
`C:\Users\Admin\.wslconfig`, chưa cài Docker Engine, chưa có `jq`, múi giờ UTC. GPU
(RTX 5060 Ti, driver 591.86) đã thấy trong WSL. Tích hợp WSL của Docker Desktop đã không còn
(`docker` không có trong WSL).

Cập nhật 16/11/2026: chủ máy đã làm xong A và B; `check_host.sh --gpu-test` → 40 PASS, 0 FAIL.

---

## A. Phía Windows (PowerShell **Run as Administrator**, tài khoản `Admin`)

### A1. Cập nhật WSL

```powershell
wsl --update
wsl --version        # WSL >= 2.x
```

### A2. `.wslconfig` — RAM, swap, không tự tắt VM

Tạo `C:\Users\Admin\.wslconfig` (mã hoá UTF-8, không BOM):

```powershell
@"
[wsl2]
memory=24GB
swap=8GB
vmIdleTimeout=-1
"@ | Set-Content -Encoding ascii "$env:USERPROFILE\.wslconfig"
```

- `memory=24GB`: để lại ~8 GB cho Windows. Tổng `mem_limit` các container (SURVEY mục 5)
  ≈ 18 GB, còn chỗ cho page cache.
- `vmIdleTimeout=-1`: không tắt VM khi không còn cửa sổ terminal nào. Cơ chế giữ VM sống
  chính là task ở A4; dòng này là lớp phụ.

Áp dụng: `wsl --shutdown` (đóng mọi cửa sổ WSL trước), rồi mở lại Ubuntu.

### A3. Tắt Docker Desktop

Production dùng Docker Engine trong WSL (service systemd, chạy khi chưa ai đăng nhập).
Docker Desktop chỉ chạy khi có người đăng nhập Windows và tranh chấp lệnh `docker`.

1. Docker Desktop → Settings → General: **bỏ** "Start Docker Desktop when you sign in".
2. Settings → Resources → WSL integration: **bỏ** Ubuntu (và "Enable integration with my
   default WSL distro").
3. Quit Docker Desktop. (Có thể gỡ hẳn qua Settings → Apps nếu không cần nữa; distro
   `docker-desktop` còn lại không ảnh hưởng.)

### A4. Khởi động WSL lúc boot, không cần đăng nhập

WSL không tự chạy khi Windows boot. Tạo Scheduled Task chạy **lúc khởi động** dưới tài khoản
`Admin`, "chạy dù người dùng có đăng nhập hay không", giữ một tiến trình `sleep infinity`
trong distro để VM không bị tắt. systemd trong distro sẽ tự khởi động Docker → các container
có `restart: unless-stopped` → cloudflared nối tunnel.

```powershell
$action  = New-ScheduledTaskAction -Execute "C:\Windows\System32\wsl.exe" `
             -Argument "-d Ubuntu -u root --exec /usr/bin/sleep infinity"
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = "PT30S"
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
             -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
             -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
             -MultipleInstances IgnoreNew
$cred = Get-Credential -UserName "$env:COMPUTERNAME\Admin" -Message "Mật khẩu Windows của Admin"
Register-ScheduledTask -TaskName "WSL-Ubuntu-KeepAlive" -Action $action -Trigger $trigger `
  -Settings $settings -RunLevel Highest `
  -User $cred.UserName -Password $cred.GetNetworkCredential().Password
```

Lưu ý:
- **Bắt buộc tài khoản có mật khẩu.** Task chạy "dù người dùng có đăng nhập hay không" phải
  lưu mật khẩu (`-LogonType Password`); Windows không cho đăng ký task kiểu này với tài khoản
  mật khẩu rỗng. Nếu `Admin` chưa có mật khẩu: đặt trước (Settings → Accounts → Sign-in
  options → Password, hoặc `net user Admin *` trong PowerShell admin) rồi mới chạy lệnh trên.
  (Máy này: `Admin` ban đầu không có mật khẩu, chủ máy đã đặt rồi tạo task — 16/11/2026.)
- Phải chạy dưới **chính tài khoản sở hữu distro** (`Admin`), không dùng `SYSTEM` (distro
  đăng ký theo user).
- Nếu `Admin` là tài khoản Microsoft: nhập **mật khẩu tài khoản Microsoft**, không phải PIN
  Windows Hello. Đổi mật khẩu Windows sau này → phải cập nhật lại task.
- Thử ngay không cần reboot: `Start-ScheduledTask WSL-Ubuntu-KeepAlive`, rồi
  `Get-ScheduledTask WSL-Ubuntu-KeepAlive | Get-ScheduledTaskInfo` (LastTaskResult
  `267009` = đang chạy là đúng).
- Không bật auto-logon Windows (không cần, và kém an toàn).

### A5. Nguồn điện / khởi động lại

```powershell
powercfg /change standby-timeout-ac 0     # không sleep khi cắm điện
powercfg /change hibernate-timeout-ac 0
powercfg /hibernate off                   # tắt luôn Fast Startup → reboot là reboot thật
```

- Windows Update cứ để tự cài và khởi động lại: sau reboot, A4 đưa hệ thống lên lại (đây
  chính là điều nghiệm thu kiểm). Có thể đặt Active hours để tránh giờ cao điểm.
- **BitLocker**: nếu có PIN trước khi boot thì máy không thể tự lên khi vắng người → chỉ
  dùng chế độ TPM-only hoặc tắt PIN pre-boot.
- (Tuỳ chọn, BIOS) bật "Restore on AC Power Loss = Power On" để máy tự bật lại sau mất điện.

### A6. Driver NVIDIA

Giữ driver Windows hiện tại (591.86, hỗ trợ Blackwell sm_120 / CUDA 13.1) hoặc mới hơn.
**Không** cài driver NVIDIA Linux bên trong WSL — WSL dùng driver Windows qua
`/usr/lib/wsl/lib`.

---

## B. Phía WSL (root: `wsl -d Ubuntu -u root`)

### B1. Cơ bản

```bash
conda config --set auto_activate_base false     # nếu shell root có conda
timedatectl set-timezone Asia/Ho_Chi_Minh
apt-get update
apt-get install -y ca-certificates curl gnupg jq
```

### B2. Docker Engine (repo chính thức của Docker)

Group `docker` (gid 1000) và user `deploy` đã có sẵn — gói cài sẽ dùng lại group đó.

```bash
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Giới hạn log container (tránh đầy đĩa) — tạo **trước** bước B3 để `nvidia-ctk` gộp vào:

```bash
cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "20m", "max-file": "5" }
}
EOF
```

### B3. NVIDIA Container Toolkit

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  > /etc/apt/sources.list.d/nvidia-container-toolkit.list
apt-get update
apt-get install -y nvidia-container-toolkit
nvidia-ctk runtime configure --runtime=docker     # thêm runtime "nvidia" vào daemon.json
cat /etc/docker/daemon.json                       # phải còn log-opts + có runtimes.nvidia
```

### B4. Bật service và thử

```bash
systemctl enable --now containerd docker
systemctl restart docker
docker run --rm hello-world
docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu24.04 nvidia-smi
```

Thoát mọi shell của `deploy` (hoặc `wsl --shutdown` từ Windows) để group `docker` có hiệu lực
cho phiên mới.

---

## C. Kiểm tra (user `deploy`)

```bash
cd /home/deploy/apps/chatbot
scripts/windows/check_host.sh --gpu-test
```

Phải ra **0 FAIL**. WARN được phép nếu đã hiểu lý do. Script không cần sudo, không đọc nội
dung `.env` / `firebase-credentials.json` (chỉ kiểm quyền 600, chủ `deploy`, đã gitignore).

## D. Thử boot không đăng nhập (sơ bộ)

Sau khi xong A–C (chưa cần app chạy):

1. Restart Windows, **không đăng nhập**, chờ 5 phút.
2. Từ máy khác không làm được gì để kiểm lúc này (chưa có tunnel) → đăng nhập, mở Ubuntu
   (user `deploy`) và xem:
   ```bash
   systemctl show docker -p ActiveEnterTimestamp   # thời điểm phải TRƯỚC lúc đăng nhập
   uptime -s
   ```
   Docker phải đã chạy từ trước khi đăng nhập ⇒ A4 hoạt động.

Nghiệm thu thật (trang truy cập được qua URL trycloudflare mới / domain, không đăng nhập
Windows) làm ở bước 7, khi đã có compose + cloudflared + deploy.sh ghi URL ra log.
