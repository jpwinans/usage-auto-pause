"""Native AppKit pacing instruments. Run directly or build with py2app."""
import math
import os
from pathlib import Path
import threading
import time

import objc
from AppKit import (
    NSApplication, NSApplicationActivationPolicyRegular, NSAppearance,
    NSAppearanceNameDarkAqua, NSBackingStoreBuffered, NSBezierPath,
    NSColor, NSFont, NSFontAttributeName, NSForegroundColorAttributeName,
    NSGradient, NSMakeRect, NSMenu, NSMenuItem, NSRectFill,
    NSSegmentedControl, NSSegmentStyleRounded, NSView, NSWindow,
    NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable,
    NSWorkspace, NSWorkspaceDidWakeNotification,
)
from Foundation import NSObject, NSString, NSTimer, NSUserDefaults
from PyObjCTools import AppHelper
from native_data import REFRESH_SECONDS, DISPLAY_MAX_AGE, fetch_provider, allowance_view

# Finder does not inherit a shell's PATH. The Codex reader needs the CLI that
# the terminal command already uses; never launch a login shell for credentials.
os.environ['PATH'] = ':'.join(dict.fromkeys([
    str(Path.home()/'.local/bin'), '/opt/homebrew/bin', '/usr/local/bin',
    '/opt/local/bin', '/usr/bin', '/bin', *os.environ.get('PATH', '').split(':')]))

WIDTH, HEIGHT = 396, 790
GREEN, YELLOW, RED = '#68d58b', '#f3c54f', '#f06555'


def color(hex_value):
    value = hex_value.lstrip('#')
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(
        int(value[0:2],16)/255, int(value[2:4],16)/255, int(value[4:6],16)/255, 1)


def text(value, x, baseline, size=7, tint='#93a098', mono=True):
    font = (NSFont.monospacedSystemFontOfSize_weight_(size, .3) if mono
            else NSFont.systemFontOfSize_weight_(size, .3))
    attrs = {NSFontAttributeName: font, NSForegroundColorAttributeName: color(tint)}
    string = NSString.stringWithString_(value)
    bounds = string.sizeWithAttributes_(attrs)
    string.drawAtPoint_withAttributes_((x-bounds.width/2, baseline-bounds.height+2), attrs)


def circle(x, y, radius, tint):
    color(tint).setFill()
    NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x-radius,y-radius,2*radius,2*radius)).fill()


def line(a, b, tint, width=1):
    path = NSBezierPath.bezierPath()
    path.moveToPoint_(a)
    path.lineToPoint_(b)
    path.setLineWidth_(width)
    color(tint).setStroke()
    path.stroke()


def position(hours, radius=105):
    fraction = (max(-8, min(8, hours))+8)/16
    angle = math.pi*(1-fraction)
    return radius*math.cos(angle), radius*math.sin(angle)


class MeterView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(MeterView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.results = {}
        self.owner = None
        self.period = NSUserDefaults.standardUserDefaults().stringForKey_('ClaudeWindow') or 'weekly'
        self.period_keys = ['session', 'weekly']
        saved = NSUserDefaults.standardUserDefaults().stringForKey_('ClaudeAllowance')
        self.allowance = saved if saved in ('all', 'fable') else 'all'
        self.switch = NSSegmentedControl.alloc().initWithFrame_(NSMakeRect(48, 739, 300, 27))
        self.switch.setSegmentCount_(2)
        self.switch.setLabel_forSegment_('5H SESSION',0)
        self.switch.setLabel_forSegment_('7D WEEKLY',1)
        self.switch.setWidth_forSegment_(146,0)
        self.switch.setWidth_forSegment_(146,1)
        self.switch.setSegmentStyle_(NSSegmentStyleRounded)
        self.switch.setFont_(NSFont.monospacedSystemFontOfSize_weight_(9,.3))
        self.switch.setSelectedSegment_(0 if self.period == 'session' else 1)
        self.switch.setTarget_(self)
        self.switch.setAction_('changeWindow:')
        self.switch.setToolTip_('Display the shared 5-hour session or selected weekly allowance')
        self.addSubview_(self.switch)
        self.allowance_switch = NSSegmentedControl.alloc().initWithFrame_(NSMakeRect(48,704,300,27))
        self.allowance_switch.setSegmentCount_(2)
        self.allowance_switch.setSegmentStyle_(NSSegmentStyleRounded)
        self.allowance_switch.setFont_(NSFont.monospacedSystemFontOfSize_weight_(9,.3))
        for i, label in enumerate(('ALL MODELS', 'FABLE')):
            self.allowance_switch.setLabel_forSegment_(label,i)
            self.allowance_switch.setWidth_forSegment_(146,i)
        self.allowance_switch.setSelectedSegment_(1 if self.allowance == 'fable' else 0)
        self.allowance_switch.setTarget_(self)
        self.allowance_switch.setAction_('changeAllowance:')
        self.allowance_switch.setToolTip_('Display All Models or Fable weekly usage; does not change any agent model')
        self.addSubview_(self.allowance_switch)
        return self

    def isFlipped(self):
        return True

    def setFrameSize_(self, size):
        objc.super(MeterView, self).setFrameSize_(size)
        # Keep the instruments circular and the native switch in the same
        # coordinate system at every window size. Center any spare space.
        scale = max(.01, min(size.width / WIDTH, size.height / HEIGHT))
        width, height = size.width / scale, size.height / scale
        self.setBounds_(NSMakeRect((WIDTH-width)/2, (HEIGHT-height)/2, width, height))
        self.setNeedsDisplay_(True)

    def changeWindow_(self, sender):
        self.period = self.period_keys[sender.selectedSegment()]
        NSUserDefaults.standardUserDefaults().setObject_forKey_(self.period,'ClaudeWindow')
        self.setNeedsDisplay_(True)

    def changeAllowance_(self, sender):
        self.allowance = 'fable' if sender.selectedSegment() == 1 else 'all'
        NSUserDefaults.standardUserDefaults().setObject_forKey_(self.allowance,'ClaudeAllowance')
        self.setNeedsDisplay_(True)

    @objc.python_method
    def update_result(self, result):
        provider = result['provider']
        if result['failed'] and provider in self.results:
            # Keep the last real sample visible with a stale label after an outage.
            previous = self.results[provider]
            for reading in previous['windows'].values():
                if reading:
                    reading['stale'] = True
            previous['checkedAt'] = result['checkedAt']
            previous['summary'] = result['summary']
            previous['problem'] = True
        else:
            self.results[provider] = result
        self.setNeedsDisplay_(True)

    @objc.python_method
    def meter(self, provider, y, period, height):
        x = 20
        frame = NSMakeRect(x,y,356,height)
        case = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(frame,23,23)
        NSGradient.alloc().initWithStartingColor_endingColor_(color('#1b241f'),color('#131916')).drawInBezierPath_angle_(case,90)
        color('#34423a').setStroke()
        case.setLineWidth_(1)
        case.stroke()
        ox, oy = x+18, y+13
        cx, cy = ox+160, oy+98
        text(provider, ox+160, oy+22, 15, '#e8eee9', False)

        for start,end,tint in [(-8,4,GREEN),(4,6,YELLOW),(6,8,RED)]:
            path = NSBezierPath.bezierPath()
            for i in range(121):
                dx,dy = position(start+(end-start)*i/120)
                if i == 0:
                    path.moveToPoint_((cx+dx,cy+dy))
                else:
                    path.lineToPoint_((cx+dx,cy+dy))
            path.setLineWidth_(14)
            color(tint).setStroke()
            path.stroke()
        circle(cx-105,cy,7,GREEN)
        circle(cx+105,cy,7,RED)
        for hours in (-8,-4,0,4,6,8):
            a,b=position(hours,93),position(hours,99)
            line((cx+a[0],cy+a[1]),(cx+b[0],cy+b[1]),'#718378',1)

        for label,px,py in [('BEHIND',55,72),('AHEAD',265,72),('−8h',55,85),
                            ('+8h',265,85),('0h',160,225),('+4h',248,190),('+6h',277,149)]:
            text(label,ox+px,oy+py)

        result = self.results.get(provider, {})
        if provider == 'Claude' and result:
            result = allowance_view(result, self.allowance)
        reading = result.get('windows',{}).get(period)
        now = time.time()
        valid = reading is not None and reading['resetsAt'] > now
        if valid:
            stale = reading['stale'] or now-reading.get('observedAt',0) > DISPLAY_MAX_AGE
            text(f"{reading['used']:.0f}%",ox+160,oy+54,14,'#dde7df')
            text('USED · STALE' if stale else 'USED',ox+160,oy+68)
            lead = reading['leadHours']
            dx,dy = position(lead,1)
            # One tapered white needle, with no arrowhead.
            needle = NSBezierPath.bezierPath()
            needle.moveToPoint_((cx-7*dx-2.2*dy,cy-7*dy+2.2*dx))
            needle.lineToPoint_((cx+91*dx,cy+91*dy))
            needle.lineToPoint_((cx-7*dx+2.2*dy,cy-7*dy-2.2*dx))
            needle.closePath()
            NSColor.whiteColor().setFill()
            needle.fill()
            pace_label = f'{lead:+.1f}h'.replace('-', '−')
            tone = '#a9e3b9' if lead < 4 else YELLOW if lead <= 6 else RED
        else:
            text('—',ox+160,oy+54,14,'#dde7df')
            text('LOADING' if not result else 'UNAVAILABLE',ox+160,oy+68)
            pace_label, tone = '—', '#93a098'
        circle(cx,cy,8,'#111b15')
        circle(cx,cy,5.5,'#728f7b')
        circle(cx-1,cy-1,3.2,'#c3d5c8')
        line((ox+93,oy+241),(ox+227,oy+241),'#2e3932')
        label = ((reading or {}).get('label') or 'QUOTA').upper() + ' PACE · HOURS'
        text(label,ox+160,oy+256)
        text(pace_label,ox+160,oy+286,28,tone)
        status = result.get('summary', 'READING QUOTAS')
        if result and now-result.get('checkedAt',0) > DISPLAY_MAX_AGE:
            status = 'STALE · FRESH QUOTA REQUIRED'
        text(status,ox+160,oy+309,8,RED if status.startswith(('PACE HOLD','STALE')) else '#93a098')

    def drawRect_(self, rect):
        color('#0b0f0d').setFill()
        NSRectFill(self.bounds())
        self.meter('Codex',16,'weekly',328)
        self.meter('Claude',360,self.period,414)


class AppDelegate(NSObject):
    def applicationDidFinishLaunching_(self, notification):
        self.busy = set()
        self.stopping = False
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0,0,WIDTH,HEIGHT),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable,
            NSBackingStoreBuffered, False)
        self.window.setTitle_('LLM Pacing')
        self.window.setContentMinSize_((297, 592.5))
        self.window.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))
        self.window.setBackgroundColor_(color('#0b0f0d'))
        self.window.setReleasedWhenClosed_(False)
        self.view = MeterView.alloc().initWithFrame_(NSMakeRect(0,0,WIDTH,HEIGHT))
        self.view.owner = self
        self.window.setContentView_(self.view)
        self.window.center()
        self.window.setFrameAutosaveName_('LLMPacingWindow')
        self.window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.installMenu()
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            REFRESH_SECONDS,self,'refresh:',None,True)
        self.timer.setTolerance_(1)
        self.display_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            5,self,'redraw:',None,True)
        NSWorkspace.sharedWorkspace().notificationCenter().addObserver_selector_name_object_(
            self,'didWake:',NSWorkspaceDidWakeNotification,None)
        self.refresh_(None)

    @objc.python_method
    def installMenu(self):
        menu = NSMenu.alloc().init()
        item = NSMenuItem.alloc().init()
        menu.addItem_(item)
        submenu = NSMenu.alloc().initWithTitle_('LLM Pacing')
        refresh = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_('Refresh Now','refresh:','r')
        refresh.setTarget_(self)
        submenu.addItem_(refresh)
        submenu.addItem_(NSMenuItem.separatorItem())
        submenu.addItem_(NSMenuItem.alloc().initWithTitle_action_keyEquivalent_('Quit LLM Pacing','terminate:','q'))
        item.setSubmenu_(submenu)
        NSApplication.sharedApplication().setMainMenu_(menu)

    def redraw_(self, sender):
        self.view.setNeedsDisplay_(True)

    def refresh_(self, sender):
        self.view.setNeedsDisplay_(True)
        for provider in ('Codex','Claude'):
            if provider not in self.busy:
                self.busy.add(provider)
                threading.Thread(target=self.fetch,args=(provider,),daemon=True).start()

    @objc.python_method
    def fetch(self, provider):
        with objc.autorelease_pool():
            result = fetch_provider(provider)
            if not self.stopping:
                self.performSelectorOnMainThread_withObject_waitUntilDone_('receive:',result,False)

    def receive_(self, result):
        self.busy.discard(result['provider'])
        self.view.update_result(result)

    def didWake_(self, notification):
        self.refresh_(None)

    def applicationShouldTerminateAfterLastWindowClosed_(self, application):
        return True

    def applicationWillTerminate_(self, notification):
        self.stopping = True
        self.timer.invalidate()
        self.display_timer.invalidate()
        NSWorkspace.sharedWorkspace().notificationCenter().removeObserver_(self)


if __name__ == '__main__':
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    AppHelper.runEventLoop()
