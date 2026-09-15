package com.hashmm.app.ui.workbench

import androidx.lifecycle.ViewModel
import com.hashmm.app.data.remote.FeedData
import com.hashmm.app.data.remote.FeedRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject

/** V218: 原生「定时任务/运行轨迹」页的共用取数（一次 /api/feed）。 */
@HiltViewModel
class NativeFeedViewModel @Inject constructor(private val repo: FeedRepository) : ViewModel() {
    suspend fun load(): FeedData = repo.feed()
}
