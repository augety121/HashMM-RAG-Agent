package com.hashmm.app.ui.remote

import android.content.Context
import android.graphics.SurfaceTexture
import android.util.AttributeSet
import android.view.TextureView
import org.webrtc.EglBase
import org.webrtc.EglRenderer
import org.webrtc.GlRectDrawer
import org.webrtc.RendererCommon
import org.webrtc.VideoFrame
import org.webrtc.VideoSink
import java.util.concurrent.CountDownLatch

/**
 * 把 WebRTC 视频渲染到 TextureView（抄 UU 远程的做法）。
 *
 * 为什么不用 SurfaceViewRenderer：SurfaceView 的画面在独立硬件层，套 Compose graphicsLayer 会黑屏、
 * View 级 scale 也不一定可靠；而 TextureView 的内容在普通视图层里，支持 setTransform(Matrix)——
 * 可以丝滑地缩放/平移画面，这正是 UU 能放大远程屏幕的原因。
 *
 * 渲染走标准 EglRenderer：onFrame 把帧交给 EglRenderer，EGL 表面取自 TextureView 的 SurfaceTexture。
 */
class TextureVideoRenderer @JvmOverloads constructor(context: Context, attrs: AttributeSet? = null) :
    TextureView(context, attrs), VideoSink, TextureView.SurfaceTextureListener {

    private val eglRenderer = EglRenderer("remote")
    private var rendererEvents: RendererCommon.RendererEvents? = null
    private var frameW = 0
    private var frameH = 0
    private var surfaceCreated = false

    fun init(sharedContext: EglBase.Context?, events: RendererCommon.RendererEvents?) {
        rendererEvents = events
        eglRenderer.init(sharedContext, EglBase.CONFIG_PLAIN, GlRectDrawer())
        surfaceTextureListener = this
        // 若已经可用（极少数情况下 attach 早于本次 init），手动补一次创建
        if (isAvailable) surfaceTexture?.let { onSurfaceTextureAvailable(it, width, height) }
    }

    fun release() {
        try { eglRenderer.release() } catch (_: Exception) {}
    }

    override fun onFrame(frame: VideoFrame) {
        val w = frame.rotatedWidth
        val h = frame.rotatedHeight
        if (w != frameW || h != frameH) {
            frameW = w; frameH = h
            try { post { rendererEvents?.onFrameResolutionChanged(frame.buffer.width, frame.buffer.height, frame.rotation) } } catch (_: Exception) {}
        }
        eglRenderer.onFrame(frame)
    }

    override fun onSurfaceTextureAvailable(surface: SurfaceTexture, width: Int, height: Int) {
        if (surfaceCreated) return
        surfaceCreated = true
        try { eglRenderer.createEglSurface(surface) } catch (_: Exception) {}
    }

    override fun onSurfaceTextureSizeChanged(surface: SurfaceTexture, width: Int, height: Int) {}

    override fun onSurfaceTextureDestroyed(surface: SurfaceTexture): Boolean {
        val latch = CountDownLatch(1)
        try { eglRenderer.releaseEglSurface { latch.countDown() } } catch (_: Exception) { latch.countDown() }
        try { latch.await() } catch (_: InterruptedException) {}
        surfaceCreated = false
        return true
    }

    override fun onSurfaceTextureUpdated(surface: SurfaceTexture) {}
}
