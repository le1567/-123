package com.opsdesk.console

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.CloudUpload
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Devices
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

sealed interface Route {
    data object DeviceList : Route
    data class Detail(val deviceId: String) : Route
}

@Composable
fun OpsDeskRoot(prefs: Prefs) {
    var configured by remember { mutableStateOf(prefs.isConfigured()) }
    if (!configured) {
        SetupScreen(prefs) { configured = true }
    } else {
        MainShell(
            prefs = prefs,
            onReset = {
                prefs.clearAll()
                configured = false
            }
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainShell(prefs: Prefs, onReset: () -> Unit) {
    var configVersion by remember { mutableIntStateOf(0) }
    val api = remember(prefs.repo, prefs.token, prefs.branch, configVersion) {
        GitHubApi(prefs.repo, prefs.token, prefs.branch)
    }
    var tab by remember { mutableIntStateOf(0) }
    var route by remember { mutableStateOf<Route>(Route.DeviceList) }
    var devices by remember { mutableStateOf<List<DeviceInfo>>(emptyList()) }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf("") }
    var refreshKey by remember { mutableIntStateOf(0) }
    val snackbar = remember { SnackbarHostState() }

    LaunchedEffect(refreshKey, tab, route) {
        if (tab == 0 && route is Route.DeviceList) {
            loading = true
            try {
                devices = RepoOps.fetchDevices(api)
                error = ""
            } catch (exc: Exception) {
                error = exc.message ?: "加载设备失败"
            } finally {
                loading = false
            }
        }
    }
    LaunchedEffect(error) {
        if (error.isNotBlank()) {
            snackbar.showSnackbar(error)
            error = ""
        }
    }

    val title = when (route) {
        Route.DeviceList -> when (tab) {
            0 -> "设备"
            1 -> "发布更新"
            else -> "设置"
        }
        is Route.Detail -> "设备详情"
    }

    Scaffold(
        containerColor = OpsBg,
        snackbarHost = { SnackbarHost(snackbar) },
        topBar = {
            TopAppBar(
                title = { Text(title, fontWeight = FontWeight.Bold) },
                navigationIcon = {
                    if (route is Route.Detail) {
                        IconButton(onClick = { route = Route.DeviceList }) {
                            Icon(Icons.Default.ArrowBack, contentDescription = "返回")
                        }
                    }
                },
                actions = {
                    if (route is Route.DeviceList && tab == 0) {
                        IconButton(onClick = { refreshKey++ }) {
                            Icon(Icons.Default.Refresh, contentDescription = "刷新")
                        }
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = OpsBg,
                    titleContentColor = OpsText,
                    navigationIconContentColor = OpsText,
                    actionIconContentColor = OpsMuted
                )
            )
        },
        bottomBar = {
            if (route is Route.DeviceList) {
                NavigationBar(containerColor = OpsPanel) {
                    NavigationBarItem(
                        selected = tab == 0,
                        onClick = { tab = 0 },
                        icon = { Icon(Icons.Default.Devices, contentDescription = null) },
                        label = { Text("设备") }
                    )
                    NavigationBarItem(
                        selected = tab == 1,
                        onClick = { tab = 1 },
                        icon = { Icon(Icons.Default.CloudUpload, contentDescription = null) },
                        label = { Text("发版") }
                    )
                    NavigationBarItem(
                        selected = tab == 2,
                        onClick = { tab = 2 },
                        icon = { Icon(Icons.Default.Settings, contentDescription = null) },
                        label = { Text("设置") }
                    )
                }
            }
        }
    ) { inner ->
        Box(Modifier.padding(inner).fillMaxSize()) {
            when (val current = route) {
                is Route.Detail -> {
                    val device = devices.firstOrNull { it.id == current.deviceId }
                    DeviceDetailScreen(
                        api = api,
                        device = device,
                        deviceId = current.deviceId,
                        onChanged = { refreshKey++ }
                    )
                }
                Route.DeviceList -> when (tab) {
                    0 -> DeviceListScreen(
                        devices = devices,
                        loading = loading,
                        onSelect = { route = Route.Detail(it.id) },
                        onRefresh = { refreshKey++ }
                    )
                    1 -> ReleaseScreen(api, prefs.repo)
                    else -> SettingsScreen(
                        prefs = prefs,
                        api = api,
                        onSaved = {
                            configVersion++
                            refreshKey++
                        },
                        onReset = onReset
                    )
                }
            }
        }
    }
}

@Composable
fun OpsCard(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    Card(
        modifier = modifier,
        colors = CardDefaults.cardColors(containerColor = OpsPanel),
        shape = RoundedCornerShape(14.dp),
        border = BorderStroke(1.dp, OpsBorder)
    ) {
        Column(Modifier.padding(16.dp), content = content)
    }
}

@Composable
fun SectionTitle(text: String, modifier: Modifier = Modifier) {
    Text(
        text = text,
        modifier = modifier,
        color = OpsMuted,
        fontSize = 12.sp,
        fontWeight = FontWeight.Bold
    )
}

@Composable
fun InfoRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 3.dp), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(label, color = OpsMuted, fontSize = 13.sp)
        Text(value, color = OpsText, fontSize = 13.sp)
    }
}

@Composable
fun SetupScreen(prefs: Prefs, onDone: () -> Unit) {
    val clipboard = LocalClipboardManager.current
    val scope = rememberCoroutineScope()
    var repo by remember { mutableStateOf(prefs.repo) }
    var branch by remember { mutableStateOf(prefs.branch) }
    var token by remember { mutableStateOf(prefs.token) }
    var publicPem by remember { mutableStateOf(if (AdminKeys.hasKey()) AdminKeys.publicPem() else "") }
    var message by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Text("OpsDesk 控制台", color = OpsText, fontSize = 26.sp, fontWeight = FontWeight.Bold)
        Text(
            "手机端通过 GitHub 私有仓库管理桌面运维终端：查看心跳、下发指令、远程锁定、发布更新。",
            color = OpsMuted,
            fontSize = 13.sp
        )

        OpsCard(Modifier.fillMaxWidth()) {
            SectionTitle("1. 连接仓库")
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(repo, { repo = it }, label = { Text("仓库 owner/repo") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(branch, { branch = it }, label = { Text("分支") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                token,
                { token = it },
                label = { Text("GitHub 细粒度令牌（Contents 读写）") },
                singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
                modifier = Modifier.fillMaxWidth()
            )
            Spacer(Modifier.height(10.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = {
                    busy = true
                    message = ""
                    scope.launch {
                        try {
                            val text = withContext(Dispatchers.IO) {
                                GitHubApi(repo, token, branch).testConnection()
                            }
                            message = text
                        } catch (exc: Exception) {
                            message = exc.message ?: "连接失败"
                        } finally {
                            busy = false
                        }
                    }
                }, enabled = !busy && token.isNotBlank() && repo.contains("/")) { Text("测试连接") }
                if (message.isNotBlank()) Text(message, color = OpsMuted, fontSize = 12.sp, modifier = Modifier.padding(top = 12.dp))
            }
        }

        OpsCard(Modifier.fillMaxWidth()) {
            SectionTitle("2. 生成管理密钥")
            Spacer(Modifier.height(6.dp))
            Text(
                "私钥保存在手机 Android Keystore，无法导出；公钥需要粘贴到每台桌面端的「管理端公钥」。",
                color = OpsMuted,
                fontSize = 12.sp
            )
            Spacer(Modifier.height(10.dp))
            if (publicPem.isBlank()) {
                Button(onClick = { publicPem = AdminKeys.publicPem() }) { Text("生成管理密钥") }
            } else {
                Text("公钥指纹：${AdminKeys.fingerprint()}", color = OpsGreen, fontSize = 12.sp)
                Spacer(Modifier.height(8.dp))
                SelectionContainer {
                    Text(
                        publicPem,
                        color = OpsText,
                        fontFamily = FontFamily.Monospace,
                        fontSize = 10.sp,
                        modifier = Modifier.padding(8.dp)
                    )
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button(onClick = { clipboard.setText(AnnotatedString(publicPem)); message = "公钥已复制" }) {
                        Icon(Icons.Default.ContentCopy, contentDescription = null)
                        Spacer(Modifier.width(6.dp))
                        Text("复制公钥")
                    }
                }
            }
        }

        Button(
            onClick = {
                if (!repo.contains("/") || token.isBlank()) {
                    message = "请填写仓库与令牌"
                    return@Button
                }
                if (publicPem.isBlank()) {
                    publicPem = AdminKeys.publicPem()
                }
                prefs.save(repo, token, branch)
                onDone()
            },
            modifier = Modifier.fillMaxWidth().height(48.dp)
        ) { Text("完成配置，进入控制台") }

        if (message.isNotBlank()) {
            Text(message, color = OpsMuted, fontSize = 12.sp)
        }
        Text(
            "提示：首次使用建议先在桌面端完成激活，并在「授权与设置」中绑定上面的公钥。",
            color = OpsMuted,
            fontSize = 11.sp
        )
        Spacer(Modifier.height(20.dp))
    }
}

@Composable
fun SettingsScreen(prefs: Prefs, api: GitHubApi, onSaved: () -> Unit, onReset: () -> Unit) {
    val clipboard = LocalClipboardManager.current
    val scope = rememberCoroutineScope()
    var repo by remember { mutableStateOf(prefs.repo) }
    var branch by remember { mutableStateOf(prefs.branch) }
    var token by remember { mutableStateOf(prefs.token) }
    var status by remember { mutableStateOf("") }
    var showReset by remember { mutableStateOf(false) }
    var showRegenerate by remember { mutableStateOf(false) }
    var publicPem by remember { mutableStateOf(AdminKeys.publicPem()) }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        OpsCard(Modifier.fillMaxWidth()) {
            SectionTitle("仓库与令牌")
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(repo, { repo = it }, label = { Text("仓库 owner/repo") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(branch, { branch = it }, label = { Text("分支") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                token,
                { token = it },
                label = { Text("GitHub 令牌") },
                singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
                modifier = Modifier.fillMaxWidth()
            )
            Spacer(Modifier.height(10.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = {
                    if (!repo.contains("/") || token.isBlank()) {
                        status = "仓库与令牌不能为空"
                        return@Button
                    }
                    prefs.save(repo, token, branch)
                    status = "已保存"
                    onSaved()
                }) { Text("保存") }
                TextButton(onClick = {
                    status = "连接中…"
                    scope.launch {
                        status = try {
                            withContext(Dispatchers.IO) { GitHubApi(repo, token, branch).testConnection() }
                        } catch (exc: Exception) {
                            exc.message ?: "连接失败"
                        }
                    }
                }) { Text("测试连接") }
            }
            if (status.isNotBlank()) {
                Text(status, color = OpsMuted, fontSize = 12.sp, modifier = Modifier.padding(top = 8.dp))
            }
        }

        OpsCard(Modifier.fillMaxWidth()) {
            SectionTitle("管理端密钥")
            Spacer(Modifier.height(6.dp))
            Text("指纹 ${AdminKeys.fingerprint()}", color = OpsGreen, fontSize = 12.sp)
            Spacer(Modifier.height(8.dp))
            SelectionContainer {
                Text(
                    publicPem,
                    color = OpsMuted,
                    fontFamily = FontFamily.Monospace,
                    fontSize = 9.sp,
                    modifier = Modifier.padding(6.dp)
                )
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = { clipboard.setText(AnnotatedString(publicPem)); status = "公钥已复制" }) {
                    Icon(Icons.Default.ContentCopy, contentDescription = null)
                    Spacer(Modifier.width(6.dp))
                    Text("复制公钥")
                }
                TextButton(onClick = { showRegenerate = true }) { Text("重新生成", color = OpsRed) }
            }
            Text(
                "重新生成后，所有桌面端都需要重新绑定新公钥，旧指令将全部失效。",
                color = OpsMuted,
                fontSize = 11.sp
            )
        }

        OpsCard(Modifier.fillMaxWidth()) {
            SectionTitle("安全说明")
            Spacer(Modifier.height(6.dp))
            Text(
                "• 令牌仅保存在手机应用私有目录，建议只授权该仓库的 Contents 读写权限。\n" +
                    "• 指令与更新清单都用管理私钥签名，桌面端验签后执行。\n" +
                    "• 桌面端连续 72 小时无法联网校验授权会自动锁定。\n" +
                    "• 手机丢失前请在 GitHub 撤销令牌，并在桌面端解绑/换绑公钥。",
                color = OpsMuted,
                fontSize = 12.sp
            )
        }

        TextButton(onClick = { showReset = true }, modifier = Modifier.fillMaxWidth()) {
            Text("清除本机配置并重新初始化", color = OpsRed)
        }
    }

    if (showReset) {
        AlertDialog(
            onDismissRequest = { showReset = false },
            title = { Text("清除配置") },
            text = { Text("将清除仓库、令牌等本机配置，管理密钥保留。确定继续？") },
            confirmButton = {
                TextButton(onClick = {
                    showReset = false
                    onReset()
                }) { Text("确定", color = OpsRed) }
            },
            dismissButton = { TextButton(onClick = { showReset = false }) { Text("取消") } }
        )
    }
    if (showRegenerate) {
        AlertDialog(
            onDismissRequest = { showRegenerate = false },
            title = { Text("重新生成管理密钥") },
            text = { Text("生成新密钥后，所有桌面端都需要重新绑定新公钥，已下发的指令会验签失败。") },
            confirmButton = {
                TextButton(onClick = {
                    showRegenerate = false
                    AdminKeys.reset()
                    publicPem = AdminKeys.publicPem()
                    status = "已生成新密钥，请重新绑定桌面端"
                }) { Text("生成", color = OpsRed) }
            },
            dismissButton = { TextButton(onClick = { showRegenerate = false }) { Text("取消") } }
        )
    }
}
