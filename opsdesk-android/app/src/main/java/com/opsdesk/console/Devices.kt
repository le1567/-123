package com.opsdesk.console

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.launch
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private fun licenseColor(state: String): Color = when (state) {
    "active" -> OpsGreen
    "grace" -> OpsAmber
    "unknown" -> OpsMuted
    else -> OpsRed
}

private fun formatTime(timestamp: Long): String {
    if (timestamp <= 0) return "—"
    return try {
        SimpleDateFormat("MM-dd HH:mm", Locale.getDefault()).format(Date(timestamp * 1000))
    } catch (_: Exception) {
        "—"
    }
}

@Composable
fun StatePill(text: String, color: Color) {
    Box(
        Modifier.background(color.copy(alpha = 0.14f), RoundedCornerShape(20.dp))
            .padding(horizontal = 8.dp, vertical = 3.dp)
    ) {
        Text(text, color = color, fontSize = 10.sp)
    }
}

@Composable
fun DeviceListScreen(
    devices: List<DeviceInfo>,
    loading: Boolean,
    onSelect: (DeviceInfo) -> Unit,
    onRefresh: () -> Unit
) {
    Column(Modifier.fillMaxSize().padding(16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("终端", color = OpsText, fontSize = 20.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.width(8.dp))
            Text("${devices.size} 台", color = OpsMuted, fontSize = 12.sp)
            Spacer(Modifier.weight(1f))
            TextButton(onClick = onRefresh) { Text("刷新") }
        }
        if (loading) {
            LinearProgressIndicator(Modifier.fillMaxWidth().padding(vertical = 6.dp))
        }
        Spacer(Modifier.height(8.dp))
        if (devices.isEmpty() && !loading) {
            OpsCard(Modifier.fillMaxWidth()) {
                Text("暂无终端心跳", color = OpsText, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(6.dp))
                Text(
                    "请确认桌面端已配置同一仓库与令牌、已绑定管理端公钥，并保持运行。心跳默认每 10 分钟上报一次。",
                    color = OpsMuted,
                    fontSize = 12.sp
                )
                Spacer(Modifier.height(10.dp))
                Button(onClick = onRefresh) { Text("重新加载") }
            }
            return@Column
        }
        LazyColumn(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            items(devices, key = { it.id }) { device ->
                DeviceCard(device) { onSelect(device) }
            }
        }
    }
}

@Composable
private fun DeviceCard(device: DeviceInfo, onClick: () -> Unit) {
    val color = if (device.online) OpsGreen else OpsMuted
    OpsCard(Modifier.fillMaxWidth().clickable(onClick = onClick)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(9.dp).background(color, CircleShape))
            Spacer(Modifier.width(8.dp))
            Column(Modifier.weight(1f)) {
                Text(device.displayName, color = OpsText, fontSize = 15.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(2.dp))
                Text(
                    "${device.os.ifBlank { "未知系统" }} · v${device.version.ifBlank { "?" }}",
                    color = OpsMuted,
                    fontSize = 11.sp,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis
                )
            }
            Column(horizontalAlignment = Alignment.End) {
                StatePill(if (device.online) "在线" else "离线", color)
                Spacer(Modifier.height(4.dp))
                Text(formatTime(device.lastSeenTs), color = OpsMuted, fontSize = 10.sp)
            }
        }
        Spacer(Modifier.height(10.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            StatePill(device.licenseLabel, licenseColor(device.licenseState))
            Spacer(Modifier.width(8.dp))
            Text(device.licenseSummary, color = OpsMuted, fontSize = 11.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        Spacer(Modifier.height(8.dp))
        Text(device.id, color = OpsMuted, fontSize = 10.sp, fontFamily = FontFamily.Monospace)
    }
}

@Composable
fun DeviceDetailScreen(
    api: GitHubApi,
    device: DeviceInfo?,
    deviceId: String,
    onChanged: () -> Unit
) {
    val scope = rememberCoroutineScope()
    val clipboard = LocalClipboardManager.current
    var current by remember(deviceId) { mutableStateOf(device) }
    var results by remember(deviceId) { mutableStateOf<List<CommandResult>>(emptyList()) }
    var sending by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf("") }
    var refreshKey by remember { mutableIntStateOf(0) }
    var pending by remember { mutableStateOf<RemoteAction?>(null) }
    var inputAction by remember { mutableStateOf<RemoteAction?>(null) }
    var inputValue by remember { mutableStateOf("") }
    var detailResult by remember { mutableStateOf<CommandResult?>(null) }
    var showRevoke by remember { mutableStateOf(false) }
    var loading by remember { mutableStateOf(device == null) }

    LaunchedEffect(device) {
        if (device != null) current = device
    }
    LaunchedEffect(deviceId, refreshKey) {
        loading = current == null
        try {
            api.getJson("${RepoOps.DEVICES_DIR}/$deviceId.json")?.let {
                current = DeviceInfo.from(deviceId, it)
            }
            results = RepoOps.fetchResults(api, deviceId)
            message = ""
        } catch (exc: Exception) {
            message = exc.message ?: "加载失败"
        } finally {
            loading = false
        }
    }

    fun dispatch(item: RemoteAction, extra: Map<String, Any?> = emptyMap()) {
        scope.launch {
            sending = true
            message = "正在下发：${item.label}"
            try {
                RepoOps.sendCommand(api, deviceId, item.action, item.params + extra)
                message = "已下发「${item.label}」，等待终端轮询执行"
                results = RepoOps.fetchResults(api, deviceId)
            } catch (exc: Exception) {
                message = exc.message ?: "下发失败"
            } finally {
                sending = false
            }
        }
    }

    fun trigger(item: RemoteAction) {
        when {
            item.inputHint != null -> {
                inputValue = ""
                inputAction = item
            }
            item.confirm != null -> pending = item
            else -> dispatch(item)
        }
    }

    val actions = remember {
        listOf(
            RemoteAction("立即体检", "sysinfo"),
            RemoteAction("完整巡检", "inspect"),
            RemoteAction("进程列表", "processes"),
            RemoteAction("服务列表", "services"),
            RemoteAction("网络信息", "network"),
            RemoteAction("查看日志", "logs"),
            RemoteAction("授权状态", "license_status"),
            RemoteAction("检查更新", "update_check"),
            RemoteAction(
                "临时文件清理",
                "disk_cleanup",
                params = mapOf("older_than_hours" to 24),
                confirm = "将删除临时目录中 24 小时前的文件，并释放磁盘空间，是否继续？",
                danger = true
            ),
            RemoteAction("重启服务", "restart_service", inputHint = "服务名"),
            RemoteAction("结束进程", "kill_process", inputHint = "PID 或进程名"),
            RemoteAction("立即安装更新", "apply_update", confirm = "终端将下载并安装最新版本后重启，是否继续？"),
            RemoteAction("远程锁定", "lock", confirm = "锁定后终端主功能将不可用，仅保留管理端解锁指令。", danger = true),
            RemoteAction("解除锁定", "unlock", confirm = "确认解除该终端的远程锁定？")
        )
    }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        if (loading) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                Spacer(Modifier.width(10.dp))
                Text("加载终端信息…", color = OpsMuted, fontSize = 12.sp)
            }
        }
        val info = current
        if (info != null) {
            OpsCard(Modifier.fillMaxWidth()) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(info.displayName, color = OpsText, fontSize = 18.sp, fontWeight = FontWeight.Bold)
                        Text(info.id, color = OpsMuted, fontSize = 10.sp, fontFamily = FontFamily.Monospace)
                    }
                    if (info.online) StatePill("在线", OpsGreen) else StatePill("离线", OpsMuted)
                }
                Spacer(Modifier.height(10.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    StatePill(info.licenseLabel, licenseColor(info.licenseState))
                    Spacer(Modifier.width(8.dp))
                    Text(info.licenseSummary, color = OpsMuted, fontSize = 12.sp)
                }
                Spacer(Modifier.height(10.dp))
                InfoRow("系统", info.os.ifBlank { "—" })
                InfoRow("客户端", "v${info.version.ifBlank { "?" }}")
                InfoRow("客户", info.customer.ifBlank { "—" })
                InfoRow("最后在线", formatTime(info.lastSeenTs))
                InfoRow("管理端公钥", if (info.adminBound) "已绑定" else "未绑定")
                InfoRow("远程脚本", if (info.allowScript) "允许" else "禁止")
                if (info.ips.isNotEmpty()) InfoRow("IP", info.ips.joinToString(", "))
                Spacer(Modifier.height(6.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    TextButton(onClick = {
                        clipboard.setText(AnnotatedString(info.id))
                        message = "设备码已复制"
                    }) {
                        Icon(Icons.Default.ContentCopy, contentDescription = null)
                        Spacer(Modifier.width(4.dp))
                        Text("复制设备码")
                    }
                    TextButton(onClick = { refreshKey++ }) { Text("刷新状态") }
                }
            }

            OpsCard(Modifier.fillMaxWidth()) {
                SectionTitle("远程操作")
                Spacer(Modifier.height(4.dp))
                Text(
                    "指令通过私有仓库队列下发，终端轮询后验签执行，默认 60 秒内生效。",
                    color = OpsMuted,
                    fontSize = 11.sp
                )
                Spacer(Modifier.height(10.dp))
                if (sending) {
                    LinearProgressIndicator(Modifier.fillMaxWidth().padding(bottom = 8.dp))
                }
                actions.chunked(2).forEach { rowItems ->
                    Row(Modifier.fillMaxWidth().padding(vertical = 3.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        rowItems.forEach { item ->
                            OutlinedButton(
                                onClick = { trigger(item) },
                                modifier = Modifier.weight(1f),
                                colors = if (item.danger) {
                                    ButtonDefaults.outlinedButtonColors(contentColor = OpsRed)
                                } else {
                                    ButtonDefaults.outlinedButtonColors(contentColor = OpsText)
                                }
                            ) {
                                Text(item.label, fontSize = 12.sp, maxLines = 1)
                            }
                        }
                        if (rowItems.size == 1) Spacer(Modifier.weight(1f))
                    }
                }
                if (message.isNotBlank()) {
                    Spacer(Modifier.height(8.dp))
                    Text(message, color = OpsMuted, fontSize = 11.sp)
                }
            }

            OpsCard(Modifier.fillMaxWidth()) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    SectionTitle("最近执行结果", Modifier.weight(1f))
                    TextButton(onClick = { refreshKey++ }) { Text("刷新") }
                }
                if (results.isEmpty()) {
                    Text("暂无指令结果", color = OpsMuted, fontSize = 12.sp, modifier = Modifier.padding(vertical = 8.dp))
                }
                results.take(10).forEach { result ->
                    Row(
                        Modifier.fillMaxWidth().clickable { detailResult = result }.padding(vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        StatePill(if (result.ok) "成功" else "失败", if (result.ok) OpsGreen else OpsRed)
                        Spacer(Modifier.width(8.dp))
                        Column(Modifier.weight(1f)) {
                            Text(result.action, color = OpsText, fontSize = 13.sp)
                            Text(result.finishedAt, color = OpsMuted, fontSize = 10.sp)
                        }
                        Text("查看", color = OpsAccent, fontSize = 12.sp)
                    }
                }
            }

            OpsCard(Modifier.fillMaxWidth()) {
                SectionTitle("授权管理")
                Spacer(Modifier.height(6.dp))
                Text(
                    if (info.licenseState == "revoked") "该设备授权已吊销，恢复后需联网校验才会解锁。"
                    else "吊销后终端会在下一次联网校验时锁定，需恢复授权并联网校验。",
                    color = OpsMuted,
                    fontSize = 11.sp
                )
                Spacer(Modifier.height(8.dp))
                OutlinedButton(
                    onClick = { showRevoke = true },
                    colors = ButtonDefaults.outlinedButtonColors(
                        contentColor = if (info.licenseState == "revoked") OpsGreen else OpsRed
                    )
                ) {
                    Text(if (info.licenseState == "revoked") "恢复授权" else "吊销授权")
                }
            }
        } else if (!loading) {
            OpsCard(Modifier.fillMaxWidth()) {
                Text("未找到该终端心跳", color = OpsText, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                Spacer(Modifier.height(6.dp))
                Text("设备可能尚未运行桌面端，或仓库路径配置不一致。", color = OpsMuted, fontSize = 12.sp)
                Spacer(Modifier.height(8.dp))
                Button(onClick = { refreshKey++ }) { Text("重试") }
            }
        }
    }

    val revokeDevice = current
    if (pending != null) {
        val item = pending!!
        AlertDialog(
            onDismissRequest = { pending = null },
            title = { Text(item.label) },
            text = { Text(item.confirm ?: "确认执行？") },
            confirmButton = {
                TextButton(onClick = {
                    pending = null
                    dispatch(item)
                }) { Text("确认") }
            },
            dismissButton = { TextButton(onClick = { pending = null }) { Text("取消") } }
        )
    }
    if (inputAction != null) {
        val item = inputAction!!
        AlertDialog(
            onDismissRequest = { inputAction = null },
            title = { Text(item.label) },
            text = {
                Column {
                    Text(item.inputHint ?: "", color = OpsMuted, fontSize = 12.sp)
                    Spacer(Modifier.height(8.dp))
                    OutlinedTextField(inputValue, { inputValue = it }, singleLine = true, label = { Text("参数") })
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    val action = item
                    val value = inputValue.trim()
                    inputAction = null
                    if (value.isBlank()) {
                        message = "参数不能为空"
                    } else {
                        val extra = when (action.action) {
                            "restart_service" -> mapOf("name" to value)
                            "kill_process" -> if (value.all { it.isDigit() }) mapOf("pid" to value.toInt()) else mapOf("name" to value)
                            else -> mapOf("value" to value)
                        }
                        dispatch(action, extra)
                    }
                }) { Text("执行") }
            },
            dismissButton = { TextButton(onClick = { inputAction = null }) { Text("取消") } }
        )
    }
    if (detailResult != null) {
        val result = detailResult!!
        AlertDialog(
            onDismissRequest = { detailResult = null },
            title = { Text("${result.action} · ${if (result.ok) "成功" else "失败"}") },
            text = {
                SelectionContainer {
                    Text(
                        result.output.ifBlank { "（无输出）" },
                        color = OpsText,
                        fontFamily = FontFamily.Monospace,
                        fontSize = 10.sp
                    )
                }
            },
            confirmButton = { TextButton(onClick = { detailResult = null }) { Text("关闭") } }
        )
    }
    if (showRevoke && revokeDevice != null) {
        val revoking = revokeDevice.licenseState != "revoked"
        AlertDialog(
            onDismissRequest = { showRevoke = false },
            title = { Text(if (revoking) "吊销授权" else "恢复授权") },
            text = {
                Text(
                    if (revoking) "吊销后该终端将在联网校验时锁定，确定继续？"
                    else "恢复后该终端需要联网校验才能解锁，确定继续？"
                )
            },
            confirmButton = {
                TextButton(onClick = {
                    showRevoke = false
                    scope.launch {
                        try {
                            message = RepoOps.setRevoked(
                                api,
                                revokeDevice.id,
                                revokeDevice.licenseId,
                                revoking,
                                "管理端操作"
                            )
                            refreshKey++
                            onChanged()
                        } catch (exc: Exception) {
                            message = exc.message ?: "操作失败"
                        }
                    }
                }) { Text("确认", color = if (revoking) OpsRed else OpsGreen) }
            },
            dismissButton = { TextButton(onClick = { showRevoke = false }) { Text("取消") } }
        )
    }
}
