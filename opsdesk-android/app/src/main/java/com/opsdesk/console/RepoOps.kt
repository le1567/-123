package com.opsdesk.console

import org.json.JSONArray
import org.json.JSONObject
import java.net.URLEncoder
import java.security.MessageDigest
import java.time.Instant
import java.time.temporal.ChronoUnit
import java.util.UUID

data class DeviceInfo(
    val id: String,
    val hostname: String,
    val instanceName: String,
    val os: String,
    val version: String,
    val lastSeenTs: Long,
    val licenseState: String,
    val licenseLabel: String,
    val licenseSummary: String,
    val licenseId: String,
    val customer: String,
    val locked: Boolean,
    val adminBound: Boolean,
    val allowScript: Boolean,
    val ips: List<String>
) {
    val displayName: String get() = instanceName.ifBlank { hostname.ifBlank { id } }
    val online: Boolean get() = System.currentTimeMillis() / 1000 - lastSeenTs < 15 * 60

    companion object {
        fun from(id: String, json: JSONObject): DeviceInfo {
            val license = json.optJSONObject("license") ?: JSONObject()
            val ipsArray = json.optJSONArray("ips") ?: JSONArray()
            val ips = (0 until ipsArray.length()).mapNotNull { ipsArray.optString(it, "").ifBlank { null } }
            return DeviceInfo(
                id = json.optString("device_id", id),
                hostname = json.optString("hostname", ""),
                instanceName = json.optString("instance_name", ""),
                os = json.optString("os", ""),
                version = json.optString("version", ""),
                lastSeenTs = json.optLong("last_seen_ts", 0L),
                licenseState = license.optString("state", "unknown"),
                licenseLabel = license.optString("label", "未知"),
                licenseSummary = license.optString("summary", ""),
                licenseId = license.optString("license_id", ""),
                customer = license.optString("customer", ""),
                locked = json.optBoolean("remote_locked", false),
                adminBound = json.optBoolean("admin_key_bound", false),
                allowScript = json.optBoolean("allow_remote_script", false),
                ips = ips
            )
        }
    }
}

data class CommandResult(
    val cmdId: String,
    val action: String,
    val ok: Boolean,
    val output: String,
    val finishedAt: String
) {
    companion object {
        fun from(json: JSONObject): CommandResult = CommandResult(
            cmdId = json.optString("cmd_id", ""),
            action = json.optString("action", ""),
            ok = json.optBoolean("ok", false),
            output = json.optString("output", ""),
            finishedAt = json.optString("finished_at", "")
        )
    }
}

data class RemoteAction(
    val label: String,
    val action: String,
    val params: Map<String, Any?> = emptyMap(),
    val confirm: String? = null,
    val danger: Boolean = false,
    val inputHint: String? = null
)

object RepoOps {
    const val DEVICES_DIR = "ops/devices"
    const val COMMANDS_DIR = "ops/commands"
    const val RESULTS_DIR = "ops/results"
    const val RELEASES_DIR = "ops/releases"
    const val FILES_DIR = "ops/releases/files"
    const val REVOKED_PATH = "ops/revoked.json"

    fun nowIso(): String = Instant.now().truncatedTo(ChronoUnit.SECONDS).toString()

    suspend fun fetchDevices(api: GitHubApi): List<DeviceInfo> {
        val items = api.listDir(DEVICES_DIR)
        return items.mapNotNull { meta ->
            val name = meta.optString("name", "")
            if (!name.endsWith(".json")) return@mapNotNull null
            val id = name.removeSuffix(".json")
            val json = try {
                api.getJson("$DEVICES_DIR/$name")
            } catch (_: Exception) {
                null
            } ?: return@mapNotNull null
            DeviceInfo.from(id, json)
        }.sortedByDescending { it.lastSeenTs }
    }

    suspend fun fetchResults(api: GitHubApi, deviceId: String): List<CommandResult> {
        val items = api.listDir("$RESULTS_DIR/$deviceId")
            .filter { it.optString("name", "").endsWith(".json") }
            .sortedByDescending { it.optString("name", "") }
            .take(20)
        return items.mapNotNull { meta ->
            try {
                api.getJson("$RESULTS_DIR/$deviceId/${meta.optString("name")}")?.let(CommandResult::from)
            } catch (_: Exception) {
                null
            }
        }
    }

    suspend fun sendCommand(
        api: GitHubApi,
        deviceId: String,
        action: String,
        params: Map<String, Any?> = emptyMap()
    ): String {
        val cmdId = "cmd-${UUID.randomUUID()}"
        val doc = JSONObject()
            .put("cmd_id", cmdId)
            .put("device_id", deviceId)
            .put("action", action)
            .put("params", JSONObject(params))
            .put("issued_at", nowIso())
        doc.put("sig", AdminKeys.sign(doc))
        api.putText(
            "$COMMANDS_DIR/$deviceId/$cmdId.json",
            doc.toString(2),
            "cmd: $action -> $deviceId"
        )
        return cmdId
    }

    suspend fun publishRelease(
        api: GitHubApi,
        repo: String,
        version: String,
        notes: String,
        fileName: String,
        bytes: ByteArray,
        mandatory: Boolean = false
    ): String {
        val path = "$FILES_DIR/$fileName"
        val existing = api.getFile(path)
        api.putFile(path, bytes, "release: $fileName", existing?.sha)
        val encodedName = URLEncoder.encode(fileName, "UTF-8").replace("+", "%20")
        val fileEntry = JSONObject()
            .put("path", fileName)
            .put("url", "https://api.github.com/repos/$repo/contents/$FILES_DIR/$encodedName")
            .put("sha256", sha256Hex(bytes))
            .put("size", bytes.size)
        val files = JSONArray().put(fileEntry)
        val manifest = JSONObject()
            .put("version", version)
            .put("channel", "stable")
            .put("released_at", nowIso())
            .put("notes", notes)
            .put("mandatory", mandatory)
            .put("files", files)
        manifest.put("sig", AdminKeys.sign(manifest))
        val manifestPath = "$RELEASES_DIR/manifest.json"
        val existingManifest = api.getFile(manifestPath)
        api.putJson(manifestPath, manifest, "release: manifest $version", existingManifest?.sha)
        return "已发布 $version，文件 $fileName（${bytes.size / 1024} KB）"
    }

    suspend fun setRevoked(
        api: GitHubApi,
        deviceId: String,
        licenseId: String,
        revoke: Boolean,
        reason: String
    ): String {
        val existing = api.getFile(REVOKED_PATH)
        val entries = mutableListOf<JSONObject>()
        if (existing != null) {
            try {
                val root = JSONObject(existing.text())
                val array = root.optJSONArray("revoked") ?: JSONArray()
                for (index in 0 until array.length()) {
                    array.optJSONObject(index)?.let { entries.add(it) }
                }
            } catch (_: Exception) {
                // 兼容直接存数组的旧格式
                try {
                    val array = JSONArray(existing.text())
                    for (index in 0 until array.length()) {
                        array.optJSONObject(index)?.let { entries.add(it) }
                    }
                } catch (_: Exception) {
                    // 忽略损坏文件，按空列表处理
                }
            }
        }
        entries.removeAll { it.optString("device_id") == deviceId }
        if (revoke) {
            entries.add(
                JSONObject()
                    .put("device_id", deviceId)
                    .put("license_id", licenseId)
                    .put("reason", reason)
                    .put("revoked_at", nowIso())
            )
        }
        val root = JSONObject().put("revoked", JSONArray().apply { entries.forEach { put(it) } })
        api.putJson(
            REVOKED_PATH,
            root,
            if (revoke) "revoke: $deviceId" else "restore: $deviceId",
            existing?.sha
        )
        return if (revoke) "已吊销 $deviceId 的授权" else "已恢复 $deviceId 的授权"
    }

    fun sha256Hex(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
}
