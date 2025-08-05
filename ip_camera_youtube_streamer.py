#!/usr/bin/env python

import gi
import time
import threading
import signal
import sys
import subprocess
import re
from urllib.parse import urlparse

gi.require_version("Gst", "1.0")
from gi.repository import Gst, GObject, GLib

class SimpleIPCameraStreamer:
    def __init__(self, rtsp_url, rtmp_url):
        self.rtsp_url = rtsp_url
        self.rtmp_url = rtmp_url
        self.pipeline = None
        self.is_running = False
        self.is_fallback_mode = False
        self.main_loop = None
        self.ping_timer = None
        self.rtsp_failure_timer = None
        self.camera_ip = self.extract_ip_from_rtsp(rtsp_url)
        self.rtsp_failed = False
        
    def extract_ip_from_rtsp(self, rtsp_url):
        """Extract IP address from RTSP URL"""
        try:
            parsed = urlparse(rtsp_url)
            return parsed.hostname
        except Exception as e:
            print(f"Error extracting IP from RTSP URL: {e}")
            return None
    
    def ping_camera(self):
        """Ping the camera IP to check connectivity"""
        if not self.camera_ip:
            print("No camera IP available for ping test")
            return False
            
        try:
            # Use ping command (works on both Linux and Windows)
            if sys.platform.startswith('win'):
                result = subprocess.run(['ping', '-n', '1', '-w', '3000', self.camera_ip], 
                                      capture_output=True, text=True, timeout=5)
            else:
                result = subprocess.run(['ping', '-c', '1', '-W', '3', self.camera_ip], 
                                      capture_output=True, text=True, timeout=5)
            
            success = result.returncode == 0
            print(f"Ping to {self.camera_ip}: {'SUCCESS' if success else 'FAILED'}")
            return success
            
        except subprocess.TimeoutExpired:
            print(f"Ping to {self.camera_ip}: TIMEOUT")
            return False
        except Exception as e:
            print(f"Ping error: {e}")
            return False
    
    def create_rtsp_pipeline(self):
        """Create the GStreamer pipeline for streaming IP camera to YouTube"""
        print("Creating RTSP pipeline...")
        
        # Let GStreamer handle pipeline cleanup automatically
        self.pipeline = Gst.Pipeline.new("ip-camera-streamer")
        
        # Create elements
        rtspsrc = Gst.ElementFactory.make("rtspsrc", "rtspsrc")
        rtph264depay = Gst.ElementFactory.make("rtph264depay", "rtph264depay")
        h264parse = Gst.ElementFactory.make("h264parse", "h264parse")
        rtppcmadepay = Gst.ElementFactory.make("rtppcmadepay", "rtppcmadepay")
        alawdec = Gst.ElementFactory.make("alawdec", "alawdec")
        voaacenc = Gst.ElementFactory.make("voaacenc", "voaacenc")
        flvmux = Gst.ElementFactory.make("flvmux", "flvmux")
        rtmpsink = Gst.ElementFactory.make("rtmpsink", "rtmpsink")
        
        # Check if all elements were created successfully
        elements = [rtspsrc, rtph264depay, h264parse, rtppcmadepay, alawdec, 
                   voaacenc, flvmux, rtmpsink]
        
        for element in elements:
            if not element:
                print("Error: Failed to create GStreamer element")
                return False
        
        # Set properties
        rtspsrc.set_property("location", self.rtsp_url)
        rtspsrc.set_property("latency", 500)
        rtspsrc.set_property("protocols", 4)  # TCP
        rtspsrc.set_property("retry", 3)
        rtspsrc.set_property("timeout", 5)
        
        flvmux.set_property("streamable", True)
        rtmpsink.set_property("location", self.rtmp_url)
        
        # Add elements to pipeline
        for element in elements:
            self.pipeline.add(element)
        
        # Link static elements
        rtph264depay.link(h264parse)
        h264parse.link(flvmux)
        rtppcmadepay.link(alawdec)
        alawdec.link(voaacenc)
        voaacenc.link(flvmux)
        flvmux.link(rtmpsink)
        
        # Connect rtspsrc signals for dynamic linking
        rtspsrc.connect("pad-added", self.on_pad_added)
        
        # Set up bus for message monitoring
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_message)
        
        print("RTSP pipeline created successfully")
        return True
    
    def create_fallback_pipeline(self):
        """Create pipeline for fallback video streaming with test pattern"""
        print("Creating fallback pipeline...")
        
        # Let GStreamer handle pipeline cleanup automatically
        self.pipeline = Gst.Pipeline.new("fallback-streamer")
        
        # Video elements
        videotestsrc = Gst.ElementFactory.make("videotestsrc", "videotestsrc")
        videoconvert = Gst.ElementFactory.make("videoconvert", "videoconvert")
        clockoverlay = Gst.ElementFactory.make("clockoverlay", "clockoverlay")
        videorate = Gst.ElementFactory.make("videorate", "videorate")
        x264enc = Gst.ElementFactory.make("x264enc", "x264enc")
        video_queue = Gst.ElementFactory.make("queue", "video_queue")
        
        # Audio elements
        audiotestsrc = Gst.ElementFactory.make("audiotestsrc", "audiotestsrc")
        audiorate = Gst.ElementFactory.make("audiorate", "audiorate")
        audioconvert = Gst.ElementFactory.make("audioconvert", "audioconvert")
        audioresample = Gst.ElementFactory.make("audioresample", "audioresample")
        voaacenc = Gst.ElementFactory.make("voaacenc", "voaacenc")
        audio_queue = Gst.ElementFactory.make("queue", "audio_queue")
        
        # Mux and sink
        flvmux = Gst.ElementFactory.make("flvmux", "flvmux")
        rtmpsink = Gst.ElementFactory.make("rtmpsink", "rtmpsink")
        
        # Check if all elements were created successfully
        elements = [videotestsrc, videoconvert, clockoverlay, videorate, x264enc, video_queue,
                   audiotestsrc, audiorate, audioconvert, audioresample, voaacenc, audio_queue,
                   flvmux, rtmpsink]
        
        for element in elements:
            if not element:
                print("Error: Failed to create GStreamer element for fallback")
                return False
        
        # Set properties
        videotestsrc.set_property("is-live", True)
        videotestsrc.set_property("pattern", 0)  # SMPTE color bars
        
        clockoverlay.set_property("halignment", "center")
        clockoverlay.set_property("valignment", "top")
        clockoverlay.set_property("font-desc", "Sans, 48")
        clockoverlay.set_property("shaded-background", True)
        
        videorate.set_property("drop-only", True)
        
        x264enc.set_property("bitrate", 4000)
        x264enc.set_property("tune", "zerolatency")
        x264enc.set_property("key-int-max", 60)
        x264enc.set_property("speed-preset", "faster")
        
        video_queue.set_property("max-size-buffers", 10)
        video_queue.set_property("leaky", 2)  # Leak downstream
        
        audiotestsrc.set_property("is-live", True)
        audiotestsrc.set_property("wave", 4)  # silence
        audiotestsrc.set_property("volume", 0.0)
        
        voaacenc.set_property("bitrate", 96000)
        
        audio_queue.set_property("max-size-buffers", 10)
        audio_queue.set_property("leaky", 2)  # Leak downstream
        
        flvmux.set_property("streamable", True)
        rtmpsink.set_property("location", self.rtmp_url)
        
        # Add elements to pipeline
        for element in elements:
            self.pipeline.add(element)
        
        # Create caps
        # video_caps = Gst.Caps.from_string("video/x-raw,width=1280,height=720,framerate=30/1")
        video_caps = Gst.Caps.from_string("video/x-raw,width=3840,height=2160,framerate=10/1")
        audio_caps = Gst.Caps.from_string("audio/x-raw,rate=48000,channels=1")
        
        # Link video elements
        videotestsrc.link_filtered(videoconvert, video_caps)
        videoconvert.link(clockoverlay)
        clockoverlay.link(videorate)
        videorate.link(x264enc)
        x264enc.link(video_queue)
        video_queue.link(flvmux)
        
        # Link audio elements
        audiotestsrc.link_filtered(audiorate, audio_caps)
        audiorate.link(audioconvert)
        audioconvert.link(audioresample)
        audioresample.link(voaacenc)
        voaacenc.link(audio_queue)
        audio_queue.link(flvmux)
        
        flvmux.link(rtmpsink)
        
        # Add bus message handler
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_message)
        
        print("Fallback pipeline created successfully")
        return True
    
    def on_pad_added(self, src, pad):
        """Handle dynamic pad creation from rtspsrc"""
        pad_caps = pad.get_current_caps()
        
        if pad_caps:
            caps_str = pad_caps.to_string()
            
            # Link video pad
            if "video" in caps_str or "H264" in caps_str:
                rtph264depay = self.pipeline.get_by_name("rtph264depay")
                if rtph264depay:
                    sink_pad = rtph264depay.get_static_pad("sink")
                    if sink_pad and not sink_pad.is_linked():
                        pad.link(sink_pad)
                        print("Video pad linked")
            
            # Link audio pad
            elif "audio" in caps_str or "PCMA" in caps_str:
                rtppcmadepay = self.pipeline.get_by_name("rtppcmadepay")
                if rtppcmadepay:
                    sink_pad = rtppcmadepay.get_static_pad("sink")
                    if sink_pad and not sink_pad.is_linked():
                        pad.link(sink_pad)
                        print("Audio pad linked")
    
    def on_message(self, bus, message):
        """Handle GStreamer bus messages"""
        t = message.type
        
        if t == Gst.MessageType.EOS:
            print("End of stream received")
            if not self.is_fallback_mode:
                self.handle_rtsp_failure()
                
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"Pipeline Error: {err}")
            if debug:
                print(f"Debug info: {debug}")
            
            # If this is an RTSP pipeline error, switch to fallback
            if not self.is_fallback_mode:
                self.handle_rtsp_failure()
                
        elif t == Gst.MessageType.ELEMENT:
            # Handle GstRTSPSrcTimeout messages
            structure = message.get_structure()
            if structure and structure.get_name() == "GstRTSPSrcTimeout":
                print("GstRTSPSrcTimeout detected!")
                if not self.is_fallback_mode:
                    self.handle_rtsp_failure()
                
        elif t == Gst.MessageType.WARNING:
            warn, debug = message.parse_warning()
            print(f"Pipeline Warning: {warn}")
            
        elif t == Gst.MessageType.STATE_CHANGED:
            if message.src == self.pipeline:
                old_state, new_state, pending_state = message.parse_state_changed()
                print(f"Pipeline state changed: {old_state.value_nick} -> {new_state.value_nick}")
    
    def handle_rtsp_failure(self):
        """Handle RTSP connection failure - wait 10 seconds then switch to fallback"""
        if self.rtsp_failed or self.is_fallback_mode:
            return
            
        print("RTSP failure detected, waiting 10 seconds before switching to fallback...")
        self.rtsp_failed = True
        
        # Cancel any existing timer
        if self.rtsp_failure_timer:
            self.rtsp_failure_timer.cancel()
        
        # Wait exactly 10 seconds then switch to fallback
        self.rtsp_failure_timer = threading.Timer(10.0, self.switch_to_fallback)
        self.rtsp_failure_timer.start()
    
    def switch_to_fallback(self):
        """Switch to fallback pipeline after RTSP failure"""
        if not self.is_running or self.is_fallback_mode:
            return
            
        print("Wait for 5 seconds then witching to fallback pipeline...")

        # Wait for 5 seconds before destroying RTSP pipeline
        time.sleep(5.0)
        
        # Destroy current RTSP pipeline completely
        if self.pipeline:
            print("Destroying RTSP pipeline...")
            ret = self.pipeline.set_state(Gst.State.NULL)
            if ret != Gst.StateChangeReturn.FAILURE:
                # Wait for state change to complete
                ret, state, pending = self.pipeline.get_state(5 * Gst.SECOND)
                print(f"RTSP pipeline destroyed with state: {state}")
            self.pipeline = None
        
        # Small delay to ensure complete cleanup
        time.sleep(0.5)
        
        # Create and start fallback pipeline
        if self.create_fallback_pipeline():
            print("Starting fallback pipeline...")
            ret = self.pipeline.set_state(Gst.State.PLAYING)
            if ret != Gst.StateChangeReturn.FAILURE:
                self.is_fallback_mode = True
                print("Switched to fallback pipeline successfully")
                # Start ping monitoring every 30 seconds
                self.start_ping_monitoring()
            else:
                print("Failed to start fallback pipeline!")
        else:
            print("Failed to create fallback pipeline!")
    
    def start_ping_monitoring(self):
        """Start periodic ping monitoring when in fallback mode"""
        if self.is_fallback_mode and self.is_running:
            print("Starting ping monitoring (30-second intervals)...")
            self.schedule_ping_test()
    
    def schedule_ping_test(self):
        """Schedule the next ping test in exactly 30 seconds"""
        if self.is_fallback_mode and self.is_running:
            self.ping_timer = threading.Timer(30.0, self.check_camera_connectivity)
            self.ping_timer.start()
    
    def check_camera_connectivity(self):
        """Check camera connectivity and switch back to RTSP immediately if available"""
        if not self.is_fallback_mode or not self.is_running:
            return
            
        print("Checking camera connectivity...")
        if self.ping_camera():
            print("Camera is back online, switching to RTSP immediately...")
            self.switch_to_rtsp()
        else:
            print("Camera still offline, continuing with fallback...")
            self.schedule_ping_test()  # Schedule next ping test
    
    def switch_to_rtsp(self):
        """Switch back to RTSP pipeline immediately when camera is available"""
        if not self.is_running or not self.is_fallback_mode:
            return
            
        print("Switching back to RTSP pipeline...")
        
        # Cancel ping timer immediately
        if self.ping_timer:
            self.ping_timer.cancel()
            self.ping_timer = None
        
        # Destroy current fallback pipeline completely
        if self.pipeline:
            print("Destroying fallback pipeline...")
            ret = self.pipeline.set_state(Gst.State.NULL)
            if ret != Gst.StateChangeReturn.FAILURE:
                # Wait for state change to complete
                ret, state, pending = self.pipeline.get_state(5 * Gst.SECOND)
                print(f"Fallback pipeline destroyed with state: {state}")
            self.pipeline = None
        
        # Small delay to ensure complete cleanup
        time.sleep(0.5)
        
        # Reset failure flag
        self.rtsp_failed = False
        
        # Create and start RTSP pipeline
        if self.create_rtsp_pipeline():
            print("Starting RTSP pipeline...")
            ret = self.pipeline.set_state(Gst.State.PLAYING)
            if ret != Gst.StateChangeReturn.FAILURE:
                self.is_fallback_mode = False
                print("Switched back to RTSP pipeline successfully")
            else:
                print("Failed to start RTSP pipeline, falling back again...")
                self.handle_rtsp_failure()
        else:
            print("Failed to create RTSP pipeline, falling back again...")
            self.handle_rtsp_failure()
    
    def start_streaming(self):
        """Start the streaming pipeline"""
        print(f"Starting streaming... Camera IP: {self.camera_ip}")
        self.is_running = True
        self.is_fallback_mode = False
        self.rtsp_failed = False
        
        # Start with RTSP pipeline
        if self.create_rtsp_pipeline():
            ret = self.pipeline.set_state(Gst.State.PLAYING)
            if ret != Gst.StateChangeReturn.FAILURE:
                print("Initial RTSP streaming started")
                return True
            else:
                print("Failed to start initial RTSP pipeline, switching to fallback...")
                self.handle_rtsp_failure()
                return True
        else:
            print("Failed to create initial RTSP pipeline")
            return False
    
    def stop_streaming(self):
        """Stop the streaming pipeline"""
        print("Stopping streaming...")
        self.is_running = False
        
        # Cancel all timers
        if self.ping_timer:
            self.ping_timer.cancel()
            self.ping_timer = None
            
        if self.rtsp_failure_timer:
            self.rtsp_failure_timer.cancel()
            self.rtsp_failure_timer = None
        
        # Stop and destroy pipeline completely
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
        
        # Quit main loop
        if self.main_loop and self.main_loop.is_running():
            self.main_loop.quit()
    
    def signal_handler(self, signum, frame):
        """Handle system signals"""
        print(f"Received signal {signum}, shutting down...")
        self.stop_streaming()
        sys.exit(0)
    
    def run(self):
        """Main run method"""
        # Set up signal handlers
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        
        if not self.start_streaming():
            print("Failed to start streaming")
            return
        
        # Create and run main loop
        self.main_loop = GLib.MainLoop()
        
        try:
            print("Starting main loop...")
            self.main_loop.run()
        except KeyboardInterrupt:
            print("Interrupted by user")
        finally:
            self.stop_streaming()

def main():
    # Configuration
    RTSP_URL = "rtsp://192.168.124.11:554/user=admin&password=&channel=1&stream=0.sdp?real_stream"
    RTMP_URL = "rtmp://a.rtmp.youtube.com/live2/b4p3-jt7f-fpzu-5uma-49kr"
    
    # Initialize GStreamer
    GObject.threads_init()
    Gst.init(None)
    
    # Create and run streamer
    streamer = SimpleIPCameraStreamer(RTSP_URL, RTMP_URL)
    streamer.run()

if __name__ == "__main__":
    main()