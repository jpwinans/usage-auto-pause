"""Render the meter's vector artwork into a macOS icon set."""
from pathlib import Path
import math
import subprocess

from AppKit import (
    NSAffineTransform, NSBezierPath, NSBitmapImageRep, NSCalibratedRGBColorSpace,
    NSColor, NSGradient, NSGraphicsContext, NSPNGFileType,
)

ROOT = Path(__file__).resolve().parent


def color(value):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(
        int(value[0:2], 16)/255, int(value[2:4], 16)/255,
        int(value[4:6], 16)/255, 1)


def disc(x, y, radius, tint):
    color(tint).setFill()
    NSBezierPath.bezierPathWithOvalInRect_(((x-radius, y-radius), (radius*2, radius*2))).fill()


def render(size, destination):
    bitmap = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, size, size, 8, 4, True, False, NSCalibratedRGBColorSpace, 0, 0)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(NSGraphicsContext.graphicsContextWithBitmapImageRep_(bitmap))
    transform = NSAffineTransform.transform()
    transform.scaleBy_(size / 1024)
    transform.concat()

    # A generous silhouette and thick arc stay recognizable at Dock sizes.
    tile = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(((64, 64), (896, 896)), 200, 200)
    NSGradient.alloc().initWithStartingColor_endingColor_(color('101a16'), color('30443a')).drawInBezierPath_angle_(tile, 90)
    color('4a6053').setStroke()
    tile.setLineWidth_(3)
    tile.stroke()
    cx, cy, radius = 512, 359, 310
    # Glass lens follows the upright dial, with a restrained reflection.
    lens = NSBezierPath.bezierPath()
    lens.moveToPoint_((cx-355, cy-12))
    for i in range(121):
        angle = math.pi * (1-i/120)
        lens.lineToPoint_((cx+355*math.cos(angle), cy+355*math.sin(angle)))
    lens.lineToPoint_((cx+355, cy-12))
    lens.closePath()
    NSGradient.alloc().initWithStartingColor_endingColor_(color('101916'), color('304a3e')).drawInBezierPath_angle_(lens, 90)
    color('526d60').setStroke()
    lens.setLineWidth_(4)
    lens.stroke()
    NSGraphicsContext.saveGraphicsState()
    lens.addClip()
    NSColor.colorWithCalibratedRed_green_blue_alpha_(.88, 1, .94, .09).setFill()
    NSBezierPath.bezierPathWithOvalInRect_(((150, 500), (780, 340))).fill()
    NSGraphicsContext.restoreGraphicsState()
    for start, end, tint in [(0, .75, '68d58b'), (.75, .875, 'f3c54f'), (.875, 1, 'ff5148')]:
        arc = NSBezierPath.bezierPath()
        for i in range(121):
            angle = math.pi * (1 - start - (end-start)*i/120)
            point = (cx+radius*math.cos(angle), cy+radius*math.sin(angle))
            (arc.moveToPoint_ if i == 0 else arc.lineToPoint_)(point)
        color(tint).setStroke()
        arc.setLineWidth_(68)
        arc.stroke()
    for i in range(17):
        angle = math.pi*(1-i/16)
        tick = NSBezierPath.bezierPath()
        tick.moveToPoint_((cx+259*math.cos(angle), cy+259*math.sin(angle)))
        tick.lineToPoint_((cx+280*math.cos(angle), cy+280*math.sin(angle)))
        color('adc1b5').setStroke()
        tick.setLineWidth_(5 if i%4 == 0 else 3)
        tick.stroke()
    disc(cx-radius, cy, 34, '68d58b')
    disc(cx+radius, cy, 34, 'ff5148')

    dx, dy = -.48, .877
    needle = NSBezierPath.bezierPath()
    needle.moveToPoint_((cx-16*dy, cy+16*dx))
    needle.lineToPoint_((cx+260*dx, cy+260*dy))
    needle.lineToPoint_((cx+16*dy, cy-16*dx))
    needle.closePath()
    color('f4fff7').setFill()
    needle.fill()
    disc(cx, cy, 38, '14281e')
    hub = NSBezierPath.bezierPathWithOvalInRect_(((cx-27, cy-27), (54, 54)))
    NSGradient.alloc().initWithStartingColor_endingColor_(color('43594b'), color('dbe9df')).drawInBezierPath_angle_(hub, 110)
    disc(cx, cy, 17, 'e2f4e8')
    NSGraphicsContext.restoreGraphicsState()
    bitmap.representationUsingType_properties_(NSPNGFileType, {}).writeToFile_atomically_(str(destination), True)


if __name__ == '__main__':
    folder = ROOT / 'assets' / 'LLMPacing.iconset'
    folder.mkdir(parents=True, exist_ok=True)
    for base in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            suffix = '@2x' if scale == 2 else ''
            render(base*scale, folder / f'icon_{base}x{base}{suffix}.png')
    subprocess.run(['/usr/bin/iconutil', '-c', 'icns', str(folder), '-o', str(folder.parent / 'LLMPacing.icns')], check=True)
