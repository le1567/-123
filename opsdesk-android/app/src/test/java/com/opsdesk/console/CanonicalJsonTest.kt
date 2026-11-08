package com.opsdesk.console

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test

class CanonicalJsonTest {
    @Test
    fun matchesPythonCanonicalJson() {
        val document = JSONObject()
            .put("z", 1)
            .put("a", "中文\"\\\n\t")
            .put("arr", JSONArray().put(true).put(JSONObject.NULL).put(2).put("x"))
            .put("nested", JSONObject().put("b", 2).put("a", 1))

        val expected =
            "{\"a\":\"中文\\\"\\\\\\n\\t\",\"arr\":[true,null,2,\"x\"],\"nested\":{\"a\":1,\"b\":2},\"z\":1}"

        assertEquals(expected, CanonicalJson.of(document))
    }
}
