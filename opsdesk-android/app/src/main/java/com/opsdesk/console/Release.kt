package com.opsdesk.console

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private fun queryDisplayName(context: Context, uri: Uri): String? {
    return try {
        context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
            if (cursor.moveToFirst()) {
                val index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                if (index >= 0) cursor.getString(index) else null
            } else null
        }
    } catch (_: Exception) {
        null
    }
}

@Composable
fun ReleaseScreen(api: GitHubApi, repo: String) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var version by remember { mutableStateOf("") }
    var notes by remember { mutableStateOf("") }
    var mandatory by remember { mutableStateOf(false) }
    var fileName by remember { mutableStateOf("") }
    var fileBytes by remember { mutableStateOf<ByteArray?>(null) }
    var message by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var currentVersion by remember { mutableStateOf("") }

    LaunchedEffect(Unit) {
        try {
            api.getJson("${RepoOps.RELEASES_DIR}/manifest.json")?.optString("version")?.let {
                if (it.isNotBlank()) currentVersion = it
            }
        } catch (_: Exception) {
            // 首次发布没有清单属于正常情况
        }
    }

    val picker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) {
            scope.launch {
                busy = true
                message = "读取文件中…"
                try {
                    val bytes = withContext(Dispatchers.IO) {
                        context.contentResolver.openInputStream(uri)?.use { it.readBytes() }
                    } ?: throw IllegalStateException("无法读取所选文件")
                    if (bytes.size > 40 * 1024 * 1024) {
                        throw IllegalStateException("文件超过 40MB，建议先精简更新包")
                    }
                    fileName = queryDisplayName(context, uri) ?: "OpsDesk-update.zip"
                    fileBytes = bytes
                    message = "已选择 $fileName（${bytes.size / 1024} KB）"
                } catch (exc: Exception) {
                    message = exc.message ?: "读取文件失败"
                    fileBytes = null
                    fileName = ""
                } finally {
                    busy = false
                }
            }
        }
    }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        OpsCard(Modifier.fillMaxWidth()) {
            SectionTitle("当前发布状态")
            Spacer(Modifier.height(6.dp))
            Text(
                if (currentVersion.isBlank()) "尚未发布过桌面端版本" else "仓库当前版本：$currentVersion",
                color = OpsText,
                fontSize = 14.sp
            )
            Spacer(Modifier.height(4.dp))
            Text(
                "更新包上传到 ops/releases/files/，清单写入 ops/releases/manifest.json；桌面端用已绑定的管理端公钥验签。",
                color = OpsMuted,
                fontSize = 11.sp
            )
        }

        OpsCard(Modifier.fillMaxWidth()) {
            SectionTitle("发布新版本")
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                version,
                { version = it },
                label = { Text("版本号，例如 1.0.1") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth()
            )
            Spacer(Modifier.height(8.dp))
            OutlinedTextField(
                notes,
                { notes = it },
                label = { Text("更新说明") },
                minLines = 3,
                modifier = Modifier.fillMaxWidth()
            )
            Spacer(Modifier.height(6.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Checkbox(mandatory, { mandatory = it })
                Text("强制更新（桌面端提示为重要更新）", color = OpsMuted, fontSize = 12.sp)
            }
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                OutlinedButton(onClick = { picker.launch(arrayOf("*/*")) }, enabled = !busy) {
                    Text("选择更新包")
                }
                if (fileName.isNotBlank()) {
                    Text(fileName, color = OpsText, fontSize = 12.sp)
                }
            }
            if (fileBytes != null) {
                Text("大小：${fileBytes!!.size / 1024} KB · 上传后自动计算 SHA-256", color = OpsMuted, fontSize = 11.sp)
            }
            Spacer(Modifier.height(12.dp))
            if (busy) {
                LinearProgressIndicator(Modifier.fillMaxWidth())
                Spacer(Modifier.height(8.dp))
            }
            Button(
                onClick = {
                    val data = fileBytes ?: return@Button
                    if (version.isBlank()) {
                        message = "请填写版本号"
                        return@Button
                    }
                    busy = true
                    message = "上传中，请保持网络连接…"
                    scope.launch {
                        try {
                            val result = withContext(Dispatchers.IO) {
                                RepoOps.publishRelease(
                                    api,
                                    repo,
                                    version.trim(),
                                    notes.trim(),
                                    fileName,
                                    data,
                                    mandatory
                                )
                            }
                            message = result
                            currentVersion = version.trim()
                            fileBytes = null
                            fileName = ""
                        } catch (exc: Exception) {
                            message = exc.message ?: "发布失败"
                        } finally {
                            busy = false
                        }
                    }
                },
                enabled = !busy && fileBytes != null && version.isNotBlank(),
                modifier = Modifier.fillMaxWidth().height(48.dp)
            ) {
                Text("签名并发布")
            }
            if (message.isNotBlank()) {
                Spacer(Modifier.height(8.dp))
                Text(message, color = OpsMuted, fontSize = 12.sp)
            }
        }

        OpsCard(Modifier.fillMaxWidth()) {
            SectionTitle("发布后如何升级终端")
            Spacer(Modifier.height(6.dp))
            Text(
                "1. 在「设备」中进入目标终端；\n" +
                    "2. 下发「检查更新」，确认发现新版本；\n" +
                    "3. 下发「立即安装更新」，桌面端校验 SHA-256 后自动替换并重启。\n\n" +
                    "更新包建议为 PyInstaller 生成的 OpsDesk 目录压缩包（zip）。",
                color = OpsMuted,
                fontSize = 12.sp
            )
        }

        if (fileBytes != null && fileName.isNotBlank()) {
            OpsCard(Modifier.fillMaxWidth()) {
                SectionTitle("待发布文件")
                Spacer(Modifier.height(6.dp))
                SelectionContainer {
                    Text(
                        "${fileName}\nSHA-256（上传时自动校验）\n版本：${version.ifBlank { "未填写" }}",
                        color = OpsText,
                        fontFamily = FontFamily.Monospace,
                        fontSize = 10.sp
                    )
                }
            }
        }
        Spacer(Modifier.width(1.dp))
    }
}
