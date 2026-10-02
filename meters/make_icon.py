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
    cx, cy, radius = 512, 665, 310
    for start, end, tint in [(0, .75, '70dc99'), (.75, .875, 'f7ce62'), (.875, 1, 'fa7b68')]:
        arc = NSBezierPath.bezierPath()
        for i in range(121):
            angle = math.pi * (1 - start - (end-start)*i/120)
            point = (cx+radius*math.cos(angle), cy-radius*math.sin(angle))
            (arc.moveToPoint_ if i == 0 else arc.lineToPoint_)(point)
        color(tint).setStroke()
        arc.setLineWidth_(68)
        arc.stroke()
    disc(cx-radius, cy, 34, '70dc99')
    disc(cx+radius, cy, 34, 'fa7b68')

    dx, dy = -.48, -.877
    needle = NSBezierPath.bezierPath()
    needle.moveToPoint_((cx-16*dy, cy+16*dx))
    needle.lineToPoint_((cx+260*dx, cy+260*dy))
    needle.lineToPoint_((cx+16*dy, cy-16*dx))
    needle.closePath()
    color('f4fff7').setFill()
    needle.fill()
    disc(cx, cy, 38, '14281e')
    disc(cx, cy, 27, '809e8b')
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
