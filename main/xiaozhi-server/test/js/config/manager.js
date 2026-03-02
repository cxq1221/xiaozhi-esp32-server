// 配置管理模块

// 生成随机MAC地址
function generateRandomMac() {
    const hexDigits = '0123456789ABCDEF';
    let mac = '';
    for (let i = 0; i < 6; i++) {
        if (i > 0) mac += ':';
        for (let j = 0; j < 2; j++) {
            mac += hexDigits.charAt(Math.floor(Math.random() * 16));
        }
    }
    return mac;
}

// 从后端测试服务器获取默认的 deviceId（优先使用 ~/.openclaw/openclaw.json 中的 xiaozhi.deviceId）
async function loadDefaultDeviceMacFromServer() {
    try {
        const origin = window.location.origin;
        const resp = await fetch(`${origin}/xiaozhi/tester/config`, { method: 'GET' });
        if (!resp.ok) {
            return null;
        }
        const data = await resp.json();
        if (data && data.ok && data.deviceId) {
            const id = String(data.deviceId).trim();
            return id || null;
        }
    } catch (e) {
        console.warn('加载默认 deviceId 失败:', e);
    }
    return null;
}

// 加载配置
export function loadConfig() {
    const deviceMacInput = document.getElementById('deviceMac');
    const deviceNameInput = document.getElementById('deviceName');
    const clientIdInput = document.getElementById('clientId');
    const otaUrlInput = document.getElementById('otaUrl');
    console.log('loadConfig');
    // 1) 优先使用本地缓存（用户成功连接后保存）
    let savedMac = localStorage.getItem('xz_tester_deviceMac');
    if (savedMac) {
        console.log('savedMac', savedMac);
        deviceMacInput.value = savedMac;
    } else {
        console.log('no savedMac');
        // 2) 没有缓存时：先尝试从后端读取 openclaw 配置；失败再随机生成
        (async () => {
            let mac = await loadDefaultDeviceMacFromServer();
            console.log('mac', mac);
            if (!mac) {
                mac = generateRandomMac();
                console.log('no mac, generateRandomMac', mac);
            }
            localStorage.setItem('xz_tester_deviceMac', mac);
            if (deviceMacInput) {
                deviceMacInput.value = mac;
            }
        })();
    }

    // 从localStorage加载其他配置
    const savedDeviceName = localStorage.getItem('xz_tester_deviceName');
    if (savedDeviceName) {
        deviceNameInput.value = savedDeviceName;
    }

    const savedClientId = localStorage.getItem('xz_tester_clientId');
    if (savedClientId) {
        clientIdInput.value = savedClientId;
    }

    const savedOtaUrl = localStorage.getItem('xz_tester_otaUrl');
    if (savedOtaUrl) {
        otaUrlInput.value = savedOtaUrl;
    }
}

// 保存配置
export function saveConfig() {
    const deviceMacInput = document.getElementById('deviceMac');
    const deviceNameInput = document.getElementById('deviceName');
    const clientIdInput = document.getElementById('clientId');

    localStorage.setItem('xz_tester_deviceMac', deviceMacInput.value);
    localStorage.setItem('xz_tester_deviceName', deviceNameInput.value);
    localStorage.setItem('xz_tester_clientId', clientIdInput.value);
}

// 获取配置值
export function getConfig() {
    // 从DOM获取值
    const deviceMac = document.getElementById('deviceMac')?.value.trim() || '';
    const deviceName = document.getElementById('deviceName')?.value.trim() || '';
    const clientId = document.getElementById('clientId')?.value.trim() || '';

    return {
        deviceId: deviceMac,  // 使用MAC地址作为deviceId
        deviceName,
        deviceMac,
        clientId
    };
}

// 保存连接URL
export function saveConnectionUrls() {
    const otaUrl = document.getElementById('otaUrl').value.trim();
    const wsUrl = document.getElementById('serverUrl').value.trim();
    localStorage.setItem('xz_tester_otaUrl', otaUrl);
    localStorage.setItem('xz_tester_wsUrl', wsUrl);
}
