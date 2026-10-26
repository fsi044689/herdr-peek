#import <Cocoa/Cocoa.h>
#import <Quartz/Quartz.h>
#include <unistd.h>

@interface QuickViewApplication : NSApplication <QLPreviewPanelDataSource, QLPreviewPanelDelegate>
@property(strong) NSURL *fileURL;
@property(strong) QLPreviewPanel *panel;
@property BOOL reportedReady;
@property NSUInteger checks;
@end

@implementation QuickViewApplication
- (void)activatePreview {
    if (@available(macOS 14.0, *)) [self activate];
    else [self activateIgnoringOtherApps:YES];
    [self.panel makeKeyAndOrderFront:nil];
}
- (BOOL)acceptsPreviewPanelControl:(QLPreviewPanel *)panel { return YES; }
- (void)beginPreviewPanelControl:(QLPreviewPanel *)panel {
    panel.dataSource = self;
    panel.delegate = self;
}
- (void)endPreviewPanelControl:(QLPreviewPanel *)panel {
    panel.dataSource = nil;
    panel.delegate = nil;
}
- (NSInteger)numberOfPreviewItemsInPreviewPanel:(QLPreviewPanel *)panel { return 1; }
- (id<QLPreviewItem>)previewPanel:(QLPreviewPanel *)panel previewItemAtIndex:(NSInteger)index {
    return self.fileURL;
}
- (void)finishLaunching {
    [super finishLaunching];
    self.panel = [QLPreviewPanel sharedPreviewPanel];
    [self activatePreview];
    [NSTimer scheduledTimerWithTimeInterval:0.05 target:self selector:@selector(checkPreview:) userInfo:nil repeats:YES];
}
- (void)checkPreview:(NSTimer *)timer {
    if (self.reportedReady) {
        if (!self.panel.visible) [self terminate:nil];
        return;
    }
    [self activatePreview];
    BOOL active = [NSWorkspace sharedWorkspace].frontmostApplication.processIdentifier == getpid();
    if (active && self.panel.keyWindow && self.panel.visible && self.panel.dataSource == self) {
        self.reportedReady = YES;
        NSDictionary *status = @{@"ready": @YES, @"pid": @(getpid()),
                                 @"system_panel": @([self.panel isKindOfClass:QLPreviewPanel.class]),
                                 @"key_window": @YES, @"active": @YES};
        NSData *data = [NSJSONSerialization dataWithJSONObject:status options:0 error:nil];
        fwrite(data.bytes, 1, data.length, stdout);
        fputc('\n', stdout);
        fflush(stdout);
    } else if (++self.checks >= 60) {
        puts("{\"error\":\"系统预览未获得焦点\"}");
        fflush(stdout);
        exit(1);
    }
}
- (BOOL)previewPanel:(QLPreviewPanel *)panel handleEvent:(NSEvent *)event {
    if (event.type == NSEventTypeKeyDown &&
        (event.keyCode == 53 || event.keyCode == 49 ||
         ((event.modifierFlags & NSEventModifierFlagCommand) && [event.charactersIgnoringModifiers isEqualToString:@"w"]))) {
        [self terminate:nil];
        return YES;
    }
    return NO;
}
- (void)windowWillClose:(NSNotification *)notification { [self terminate:nil]; }
@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSURL *url = nil;
        if (argc == 3 && strcmp(argv[1], "--url") == 0) {
            NSString *address = [NSString stringWithUTF8String:argv[2]];
            if (@available(macOS 14.0, *)) url = [NSURL URLWithString:address encodingInvalidCharacters:YES];
            else url = [NSURL URLWithString:address];
            NSString *scheme = url.scheme.lowercaseString;
            if (!url || !([scheme isEqualToString:@"http"] || [scheme isEqualToString:@"https"]) ||
                !url.host.length || url.user || url.password) {
                puts("{\"error\":\"无效的 HTTP(S) 链接\"}");
                return 2;
            }
        } else if (argc == 2) {
            url = [NSURL fileURLWithPath:[NSString stringWithUTF8String:argv[1]]];
            if (![url checkResourceIsReachableAndReturnError:nil]) return 2;
        } else return 2;
        QuickViewApplication *app = [QuickViewApplication sharedApplication];
        app.fileURL = url;
        [app setActivationPolicy:NSApplicationActivationPolicyAccessory];
        [app run];
    }
    return 0;
}
