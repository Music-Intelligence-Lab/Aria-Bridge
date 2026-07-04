#include "PluginEditor.h"

#include <cmath>

namespace
{
const juce::Colour ground      = juce::Colour::fromRGB(10, 11, 20);
const juce::Colour panelFill   = juce::Colour::fromRGB(18, 19, 31);
const juce::Colour knobDark    = juce::Colour::fromRGB(12, 13, 24);
const juce::Colour buttonDark  = juce::Colour::fromRGB(22, 24, 40);
const juce::Colour ink         = juce::Colour::fromRGB(238, 241, 255);
const juce::Colour muted        = juce::Colour::fromRGB(118, 124, 160);
const juce::Colour lineColour  = juce::Colour::fromRGB(35, 38, 62);

const juce::Colour cyan   = juce::Colour::fromRGB(56, 224, 230);   // sampling
const juce::Colour mint   = juce::Colour::fromRGB(79, 227, 154);   // transport panel + play
const juce::Colour amber  = juce::Colour::fromRGB(255, 194, 74);   // feedback + generation bar
const juce::Colour peri   = juce::Colour::fromRGB(139, 139, 255);  // playback bar
const juce::Colour coral  = juce::Colour::fromRGB(255, 107, 107);  // record
const juce::Colour orange = juce::Colour::fromRGB(255, 140, 66);   // cancel

juce::String formatFloatValue(double value) { return juce::String(value, 2); }
juce::String formatIntValue(double value)   { return juce::String(juce::roundToInt(value)); }

juce::String formatClock(int totalSeconds)
{
    const int s = juce::jmax(0, totalSeconds);
    return juce::String(s / 60) + ":" + juce::String(s % 60).paddedLeft('0', 2);
}

juce::Colour stateColour(const juce::String& status)
{
    if (status == "RECORDING")  return coral;
    if (status == "GENERATING") return amber;
    if (status == "PLAYING")    return peri;
    if (status == "IDLE" || status == "READY") return cyan;
    if (status == "DISCONNECTED") return muted;
    return ink;
}
}

void AriaLookAndFeel::drawRotarySlider(juce::Graphics& g,
                                       int x, int y, int width, int height,
                                       float sliderPosProportional,
                                       float rotaryStartAngle,
                                       float rotaryEndAngle,
                                       juce::Slider& slider)
{
    const auto accent = slider.findColour(juce::Slider::rotarySliderFillColourId);
    const bool hero = static_cast<bool>(slider.getProperties().getWithDefault("hero", false));

    const auto bounds = juce::Rectangle<float>((float) x, (float) y, (float) width, (float) height);
    const auto centre = bounds.getCentre();
    const auto boundsRadius = juce::jmin(bounds.getWidth(), bounds.getHeight()) * 0.5f;
    const auto rBody = boundsRadius * 0.70f;
    const auto rArc  = boundsRadius * 0.90f;
    const auto angle = rotaryStartAngle + sliderPosProportional * (rotaryEndAngle - rotaryStartAngle);
    const auto knobBounds = juce::Rectangle<float>(centre.x - rBody, centre.y - rBody, rBody * 2.0f, rBody * 2.0f);

    // Line weights scale with the knob so big knobs don't look thin.
    const float arcStroke = juce::jmax(2.5f, rArc * 0.13f);
    const float indStroke = juce::jmax(2.0f, rBody * 0.16f);

    g.setColour(hero ? accent : knobDark);
    g.fillEllipse(knobBounds);
    g.setColour(hero ? juce::Colours::white.withAlpha(0.10f) : lineColour);
    g.drawEllipse(knobBounds, juce::jmax(1.0f, boundsRadius * 0.05f));

    juce::Path track;
    track.addCentredArc(centre.x, centre.y, rArc, rArc, 0.0f, rotaryStartAngle, rotaryEndAngle, true);
    g.setColour(accent.withAlpha(0.18f));
    g.strokePath(track, juce::PathStrokeType(arcStroke, juce::PathStrokeType::curved, juce::PathStrokeType::rounded));

    juce::Path value;
    value.addCentredArc(centre.x, centre.y, rArc, rArc, 0.0f, rotaryStartAngle, angle, true);
    g.setColour(accent);
    g.strokePath(value, juce::PathStrokeType(arcStroke, juce::PathStrokeType::curved, juce::PathStrokeType::rounded));

    const auto tip = centre.getPointOnCircumference(rBody * 0.82f, angle);
    const auto tail = centre.getPointOnCircumference(rBody * 0.12f, angle);
    g.setColour(hero ? knobDark : ink);
    g.drawLine({ tail, tip }, indStroke);
}

void AriaLookAndFeel::drawButtonBackground(juce::Graphics& g,
                                           juce::Button& button,
                                           const juce::Colour& backgroundColourToUse,
                                           bool isMouseOverButton,
                                           bool isButtonDown)
{
    const bool on = button.getToggleState();
    const auto accent = button.findColour(juce::TextButton::buttonColourId);
    auto bounds = button.getLocalBounds().toFloat().reduced(0.5f);
    const float corner = juce::jmax(6.0f, bounds.getHeight() * 0.18f);

    auto fill = on ? backgroundColourToUse : buttonDark;
    if (! on && isMouseOverButton)
        fill = fill.interpolatedWith(accent, 0.14f);
    if (isButtonDown)
        fill = fill.darker(0.18f);

    g.setColour(fill);
    g.fillRoundedRectangle(bounds, corner);

    g.setColour(on ? backgroundColourToUse : accent);
    g.drawRoundedRectangle(bounds, corner, 1.5f);
}

void AriaLookAndFeel::drawButtonText(juce::Graphics& g, juce::TextButton& button, bool, bool)
{
    const bool on = button.getToggleState();
    g.setColour(on ? juce::Colour::fromRGB(12, 20, 22) : button.findColour(juce::TextButton::buttonColourId));
    // Font scales with the button height so text grows with the window.
    g.setFont(juce::Font(juce::jlimit(9.0f, 24.0f, (float) button.getHeight() * 0.34f), juce::Font::bold));
    g.drawFittedText(button.getButtonText().toUpperCase(), button.getLocalBounds(), juce::Justification::centred, 1);
}

AriaBridgeAudioProcessorEditor::AriaBridgeAudioProcessorEditor(AriaBridgeAudioProcessor& processor)
    : AudioProcessorEditor(&processor),
      audioProcessor(processor)
{
    setLookAndFeel(&lookAndFeel);
    windowConstrainer.setMinimumSize(560, 300);
    windowConstrainer.setMaximumSize(1500, 760);

    configureKnob(tempSlider, tempLabel, tempValueLabel, "temp", 0.1, 2.0, 0.001, 1.00, "/aria/temp",
                  AriaBridgeAudioProcessor::ControlId::temp, cyan, false);
    configureKnob(topPSlider, topPLabel, topPValueLabel, "top_p", 0.1, 1.0, 0.001, 0.98, "/aria/top_p",
                  AriaBridgeAudioProcessor::ControlId::topP, cyan, false);
    configureKnob(minPSlider, minPLabel, minPValueLabel, "min_p", 0.0, 0.3, 0.001, 0.01, "/aria/min_p",
                  AriaBridgeAudioProcessor::ControlId::minP, cyan, false);
    configureKnob(tokensSlider, tokensLabel, tokensValueLabel, "tokens", 0, 2048, 1.0, 1000, "/aria/tokens",
                  AriaBridgeAudioProcessor::ControlId::tokens, cyan, true);   // hero
    configureKnob(gradeSlider, gradeLabel, gradeValueLabel, "grade", 1, 5, 1.0, 3, "/aria/grade",
                  AriaBridgeAudioProcessor::ControlId::grade, amber, false);

    // Distinct colours per transport action.
    configureActionButton(recordButton, "record", coral);
    configureActionButton(playButton, "play", mint);
    configureActionButton(cancelButton, "cancel", orange);
    configureActionButton(syncButton, "sync", muted);
    configureActionButton(commitButton, "commit", amber);

    recordButton.onContextMenuRequested = [this] { showButtonContextMenu(AriaBridgeAudioProcessor::ControlId::record); };
    playButton.onContextMenuRequested   = [this] { showButtonContextMenu(AriaBridgeAudioProcessor::ControlId::play); };
    cancelButton.onContextMenuRequested = [this] { showButtonContextMenu(AriaBridgeAudioProcessor::ControlId::cancel); };
    syncButton.onContextMenuRequested   = [this] { showButtonContextMenu(AriaBridgeAudioProcessor::ControlId::sync); };
    commitButton.onContextMenuRequested = [this] { showButtonContextMenu(AriaBridgeAudioProcessor::ControlId::commit); };

    recordButton.setClickingTogglesState(true);
    recordButton.onClick = [this]
    {
        isRecordEnabled = recordButton.getToggleState();
        audioProcessor.sendOSC("/aria/record", isRecordEnabled ? 1 : 0);
    };

    syncButton.onClick = [this]
    {
        audioProcessor.sendOSC("/aria/temp", static_cast<float>(tempSlider.getValue()));
        audioProcessor.sendOSC("/aria/top_p", static_cast<float>(topPSlider.getValue()));
        audioProcessor.sendOSC("/aria/min_p", static_cast<float>(minPSlider.getValue()));
        audioProcessor.sendOSC("/aria/tokens", juce::roundToInt(tokensSlider.getValue()));
        audioProcessor.sendOSC("/aria/grade", juce::roundToInt(gradeSlider.getValue()));
        audioProcessor.sendOSC("/aria/ping");
    };

    commitButton.onClick = [this] { audioProcessor.sendOSC("/aria/commit"); };
    playButton.onClick = [this] { audioProcessor.sendOSC("/aria/play"); };

    // Cancel: fire cancel to the backend AND reset local UI so it visibly responds.
    cancelButton.onClick = [this]
    {
        audioProcessor.sendOSC("/aria/cancel");
        audioProcessor.sendOSC("/cancel_playback");
        setGenerationActive(false);
        stopPlayback();
        if (recordButton.getToggleState())
        {
            recordButton.setToggleState(false, juce::dontSendNotification);
            isRecordEnabled = false;
        }
    };

    statusLabel.setJustificationType(juce::Justification::centredLeft);
    statusLabel.setColour(juce::Label::textColourId, cyan);
    statusLabel.setFont(juce::Font(14.0f, juce::Font::bold));
    addAndMakeVisible(statusLabel);

    logLabel.setJustificationType(juce::Justification::centredRight);
    logLabel.setColour(juce::Label::textColourId, muted);
    logLabel.setFont(juce::Font(11.5f));
    addAndMakeVisible(logLabel);

    progressLabel.setJustificationType(juce::Justification::centred);
    progressLabel.setColour(juce::Label::textColourId, muted);
    progressLabel.setFont(juce::Font(10.5f));
    addAndMakeVisible(progressLabel);

    setResizable(true, false);
    setResizeLimits(560, 300, 1500, 760);
    setSize(760, 360);
    refreshStatusDisplay();
    audioProcessor.setEditor(this);
    configureStandaloneWindowIfNeeded();
    startTimerHz(30);
}

AriaBridgeAudioProcessorEditor::~AriaBridgeAudioProcessorEditor()
{
    stopTimer();
    setLookAndFeel(nullptr);
    audioProcessor.clearEditor(this);
}

void AriaBridgeAudioProcessorEditor::drawPanel(juce::Graphics& g, juce::Rectangle<int> r, juce::Colour accent, const juce::String& head)
{
    const auto rf = r.toFloat();
    juce::DropShadow(accent.withAlpha(0.28f), 16, { 0, 0 }).drawForRectangle(g, r);
    g.setColour(panelFill);
    g.fillRoundedRectangle(rf, 12.0f);
    g.setColour(juce::Colours::white.withAlpha(0.03f));
    g.drawRoundedRectangle(rf.reduced(1.0f), 11.0f, 1.0f);
    g.setColour(accent.withAlpha(0.85f));
    g.drawRoundedRectangle(rf.reduced(0.75f), 12.0f, 1.5f);

    g.setColour(accent);
    g.setFont(juce::Font(11.0f * uiScale, juce::Font::bold));
    g.drawText(head.toUpperCase(), r.withHeight(juce::roundToInt(18 * uiScale)).translated(0, juce::roundToInt(7 * uiScale)),
               juce::Justification::centred);
}

void AriaBridgeAudioProcessorEditor::paint(juce::Graphics& g)
{
    const float s = uiScale;
    g.fillAll(ground);

    // Header wordmark + meta.
    auto area = getLocalBounds().reduced(juce::roundToInt(14 * s));
    auto header = area.removeFromTop(juce::roundToInt(52 * s));
    juce::Font brand(30.0f * s, juce::Font::bold);
    g.setFont(brand);
    const int aw = brand.getStringWidth("Aria");
    g.setColour(ink);
    g.drawText("Aria", header.getX(), header.getY() + juce::roundToInt(2 * s), aw + 4, juce::roundToInt(34 * s), juce::Justification::centredLeft);
    g.setColour(cyan);
    g.drawText("Bridge", header.getX() + aw + 2, header.getY() + juce::roundToInt(2 * s), juce::roundToInt(240 * s), juce::roundToInt(34 * s), juce::Justification::centredLeft);
    g.setColour(muted);
    g.setFont(juce::Font(9.0f * s, juce::Font::bold));
    g.drawText("REAL-TIME CONTINUATION", header.getX() + 2, header.getY() + juce::roundToInt(36 * s), juce::roundToInt(300 * s), juce::roundToInt(12 * s), juce::Justification::centredLeft);
    g.setFont(juce::Font(10.5f * s));
    g.drawText("v0.2  \xc2\xb7  OSC 9000/9001", header.removeFromRight(juce::roundToInt(220 * s)), juce::Justification::topRight);

    // Panels — the play panel head flips to GEN while generating.
    drawPanel(g, samplingPanel,  cyan,  "Sampling");
    drawPanel(g, transportPanel, mint,  "Transport");
    drawPanel(g, feedbackPanel,  amber, "Feedback");
    drawPanel(g, playbackPanel,  peri,  isGenerating ? "Gen" : "Play");

    // Generating sweep across the sampling panel.
    if (isGenerating)
    {
        juce::Graphics::ScopedSaveState save(g);
        g.reduceClipRegion(samplingPanel.reduced(2));
        const float w = (float) samplingPanel.getWidth();
        const float sweepX = samplingPanel.getX() + (std::sin(phase * 0.06f) * 0.5f + 0.5f) * w * 1.6f - w * 0.3f;
        juce::ColourGradient grad(juce::Colours::transparentBlack, sweepX - 60.0f, 0,
                                  juce::Colours::transparentBlack, sweepX + 60.0f, 0, false);
        grad.addColour(0.5, cyan.withAlpha(0.16f));
        g.setGradientFill(grad);
        g.fillRect(samplingPanel);
    }

    // Record pulse ring.
    if (isRecordEnabled)
    {
        const float a = 0.30f + 0.30f * (float) std::sin(phase * 0.22);
        g.setColour(coral.withAlpha(a));
        g.drawRoundedRectangle(recordButton.getBounds().toFloat().expanded(3.0f * s), 9.0f, 2.5f * s);
    }

    // Vertical bar — generation progress (amber) or playback progress (periwinkle).
    {
        const bool generating = isGenerating;
        const auto barColour = generating ? amber : peri;
        const int barW = juce::roundToInt(24 * s);
        auto bar = juce::Rectangle<float>((float) (barRect.getCentreX() - barW / 2), (float) barRect.getY(),
                                          (float) barW, (float) barRect.getHeight());
        g.setColour(knobDark);
        g.fillRoundedRectangle(bar, 7.0f);
        g.setColour(lineColour);
        g.drawRoundedRectangle(bar, 7.0f, 1.5f);

        const float fillH = bar.getHeight() * (float) juce::jlimit(0.0, 1.0, playbackDisplayed);
        if (fillH > 1.0f)
        {
            auto fr = bar.withTop(bar.getBottom() - fillH);
            juce::Graphics::ScopedSaveState save(g);
            juce::Path clip;
            clip.addRoundedRectangle(bar, 7.0f);
            g.reduceClipRegion(clip);

            juce::ColourGradient grad(barColour.brighter(0.25f), fr.getX(), fr.getY(), barColour, fr.getX(), fr.getBottom(), false);
            g.setGradientFill(grad);
            g.fillRect(fr);
            g.setColour(barColour.withAlpha(0.35f));
            g.fillRect(fr.withHeight(2.0f));   // bright leading edge

            if (generating || isPlaying)
            {
                const float sh = (float) std::fmod(phase * 0.03, 1.0);
                const float bandY = fr.getBottom() - sh * fr.getHeight();
                g.setColour(juce::Colours::white.withAlpha(0.22f));
                g.fillRect(fr.getX(), bandY - 6.0f, fr.getWidth(), 12.0f);
            }
        }
    }

    // Status dot (breathing when connected).
    {
        const float a = isConnected ? (0.55f + 0.35f * (float) std::sin(phase * 0.12)) : 1.0f;
        const float dotR = 5.0f * s;
        auto d = juce::Rectangle<float>((float) statusStrip.getX() + 14.0f * s, (float) statusStrip.getCentreY() - dotR, dotR * 2.0f, dotR * 2.0f);
        g.setColour((isConnected ? mint : muted).withAlpha(a * 0.4f));
        g.fillEllipse(d.expanded(3.0f * s));
        g.setColour((isConnected ? mint : muted).withAlpha(a));
        g.fillEllipse(d);
    }

    // MIDI-learn highlight rings.
    const auto buttonBase = static_cast<int>(AriaBridgeAudioProcessor::ControlId::record);
    const auto totalCount = static_cast<int>(AriaBridgeAudioProcessor::ControlId::count);

    for (int index = 0; index < buttonBase; ++index)
    {
        const auto controlId = static_cast<AriaBridgeAudioProcessor::ControlId>(index);
        if (! audioProcessor.isLearningControl(controlId)) continue;
        g.setColour(amber);
        g.drawEllipse(getSliderForControl(controlId).getBounds().toFloat().expanded(3.0f * s), 2.5f * s);
    }
    for (int index = buttonBase; index < totalCount; ++index)
    {
        const auto controlId = static_cast<AriaBridgeAudioProcessor::ControlId>(index);
        if (! audioProcessor.isLearningControl(controlId)) continue;
        g.setColour(amber);
        g.drawRoundedRectangle(getButtonForControl(controlId).getBounds().toFloat().reduced(1.0f), 8.0f, 2.5f * s);
    }
}

void AriaBridgeAudioProcessorEditor::parentHierarchyChanged()
{
    AudioProcessorEditor::parentHierarchyChanged();
    configureStandaloneWindowIfNeeded();
}

void AriaBridgeAudioProcessorEditor::layoutKnob(LearnableSlider& slider, juce::Label& nameLabel, juce::Label& valueLabel,
                                                juce::Rectangle<int> cell, int knobSize)
{
    const int nameH = juce::roundToInt(14 * uiScale);
    const int valueH = juce::roundToInt(15 * uiScale);
    nameLabel.setBounds(cell.getX(), cell.getY(), cell.getWidth(), nameH);
    valueLabel.setBounds(cell.getX(), cell.getBottom() - valueH, cell.getWidth(), valueH);

    const int bandTop = cell.getY() + nameH + 2;
    const int bandBottom = cell.getBottom() - valueH - 2;
    const int band = juce::jmax(10, bandBottom - bandTop);
    const int size = juce::jmin(knobSize, band, cell.getWidth() - 4);
    slider.setBounds(cell.getX() + (cell.getWidth() - size) / 2, bandTop + (band - size) / 2, size, size);
}

void AriaBridgeAudioProcessorEditor::resized()
{
    uiScale = juce::jlimit(0.6f, 3.0f, juce::jmin(getWidth() / 760.0f, getHeight() / 360.0f));
    const float s = uiScale;

    auto area = getLocalBounds().reduced(juce::roundToInt(14 * s));
    area.removeFromTop(juce::roundToInt(52 * s));                 // header (drawn in paint)
    statusStrip = area.removeFromBottom(juce::roundToInt(38 * s));
    area.removeFromTop(juce::roundToInt(6 * s));
    area.removeFromBottom(juce::roundToInt(6 * s));

    const int gap = juce::roundToInt(10 * s);
    auto rack = area;
    const int w = rack.getWidth();
    samplingPanel  = rack.removeFromLeft(juce::roundToInt(w * 0.46f)); rack.removeFromLeft(gap);
    transportPanel = rack.removeFromLeft(juce::roundToInt(w * 0.21f)); rack.removeFromLeft(gap);
    feedbackPanel  = rack.removeFromLeft(juce::roundToInt(w * 0.17f)); rack.removeFromLeft(gap);
    playbackPanel  = rack;

    // Scale the knob name/value label fonts with the window.
    const auto nameFont = juce::Font(11.0f * s, juce::Font::bold);
    const auto valueFont = juce::Font(12.5f * s, juce::Font::bold);
    for (auto* l : { &tempLabel, &topPLabel, &minPLabel, &tokensLabel, &gradeLabel })
        l->setFont(nameFont);
    for (auto* l : { &tempValueLabel, &topPValueLabel, &minPValueLabel, &tokensValueLabel, &gradeValueLabel })
        l->setFont(valueFont);

    // Sampling: 4 knobs, order tokens/temp/top_p/min_p (tokens filled).
    {
        auto inner = samplingPanel.reduced(juce::roundToInt(12 * s));
        inner.removeFromTop(juce::roundToInt(16 * s));
        const int cw = inner.getWidth() / 4;
        std::array<std::tuple<LearnableSlider*, juce::Label*, juce::Label*>, 4> ks {{
            { &tokensSlider, &tokensLabel, &tokensValueLabel },
            { &tempSlider, &tempLabel, &tempValueLabel },
            { &topPSlider, &topPLabel, &topPValueLabel },
            { &minPSlider, &minPLabel, &minPValueLabel }
        }};
        for (int i = 0; i < 4; ++i)
        {
            auto cell = inner.withX(inner.getX() + i * cw).withWidth(i == 3 ? inner.getRight() - (inner.getX() + i * cw) : cw);
            layoutKnob(*std::get<0>(ks[(size_t) i]), *std::get<1>(ks[(size_t) i]), *std::get<2>(ks[(size_t) i]), cell, juce::roundToInt(58 * s));
        }
    }

    // Transport: record (tall) / play+cancel / sync.
    {
        auto inner = transportPanel.reduced(juce::roundToInt(12 * s));
        inner.removeFromTop(juce::roundToInt(16 * s));
        const int h = inner.getHeight();
        const int pad = juce::roundToInt(8 * s);
        recordButton.setBounds(inner.removeFromTop(juce::roundToInt(h * 0.34f)));
        inner.removeFromTop(pad);
        auto row = inner.removeFromTop(juce::roundToInt(h * 0.28f));
        const int halfW = (row.getWidth() - pad) / 2;
        playButton.setBounds(row.removeFromLeft(halfW));
        row.removeFromLeft(pad);
        cancelButton.setBounds(row);
        inner.removeFromTop(pad);
        syncButton.setBounds(inner.removeFromTop(juce::jmin(juce::roundToInt(28 * s), inner.getHeight())));
    }

    // Feedback: grade knob + commit.
    {
        auto inner = feedbackPanel.reduced(juce::roundToInt(12 * s));
        inner.removeFromTop(juce::roundToInt(16 * s));
        commitButton.setBounds(inner.removeFromBottom(juce::roundToInt(32 * s)));
        inner.removeFromBottom(juce::roundToInt(8 * s));
        layoutKnob(gradeSlider, gradeLabel, gradeValueLabel, inner, juce::roundToInt(62 * s));
    }

    // Playback: vertical bar + label.
    {
        auto inner = playbackPanel.reduced(juce::roundToInt(10 * s));
        inner.removeFromTop(juce::roundToInt(16 * s));
        progressLabel.setBounds(inner.removeFromBottom(juce::roundToInt(15 * s)));
        inner.removeFromBottom(juce::roundToInt(3 * s));
        barRect = inner;
    }

    // Status strip.
    {
        auto sc = statusStrip.reduced(juce::roundToInt(12 * s), juce::roundToInt(6 * s));
        sc.removeFromLeft(juce::roundToInt(24 * s));   // dot
        statusLabel.setFont(juce::Font(juce::jmax(12.0f, 14.0f * s), juce::Font::bold));
        logLabel.setFont(juce::Font(juce::jmax(10.5f, 11.5f * s)));
        progressLabel.setFont(juce::Font(juce::jmax(9.5f, 10.5f * s)));
        statusLabel.setBounds(sc.removeFromLeft(juce::roundToInt(sc.getWidth() * 0.38f)));
        logLabel.setBounds(sc);
    }
}

void AriaBridgeAudioProcessorEditor::refreshStatusDisplay()
{
    const auto snapshot = audioProcessor.getOSCStateSnapshot();
    isConnected = snapshot.connected;
    const juce::String shown = isConnected ? snapshot.currentStatus : juce::String("DISCONNECTED");
    statusLabel.setText(shown, juce::dontSendNotification);
    statusLabel.setColour(juce::Label::textColourId, stateColour(shown));
    logLabel.setText(snapshot.lastLog, juce::dontSendNotification);
    repaint();
}

void AriaBridgeAudioProcessorEditor::applyMappedControlValue(AriaBridgeAudioProcessor::ControlId controlId, double value)
{
    getSliderForControl(controlId).setValue(value, juce::sendNotificationSync);
}

void AriaBridgeAudioProcessorEditor::configureKnob(LearnableSlider& slider,
                                                   juce::Label& nameLabel,
                                                   juce::Label& valueLabel,
                                                   const juce::String& name,
                                                   double minimum, double maximum, double interval,
                                                   double defaultValue,
                                                   const juce::String& oscAddress,
                                                   AriaBridgeAudioProcessor::ControlId controlId,
                                                   juce::Colour accent,
                                                   bool hero)
{
    const bool isInt = (interval >= 1.0);
    slider.setSliderStyle(juce::Slider::RotaryVerticalDrag);
    slider.setTextBoxStyle(juce::Slider::NoTextBox, false, 0, 0);
    slider.setRange(minimum, maximum, interval);
    slider.setValue(defaultValue, juce::dontSendNotification);
    slider.setColour(juce::Slider::rotarySliderFillColourId, accent);
    if (hero)
        slider.getProperties().set("hero", true);
    slider.onValueChange = [this, controlId] { refreshValueLabel(controlId); };
    slider.onDragEnd = [this, &slider, oscAddress, isInt]
    {
        if (isInt) audioProcessor.sendOSC(oscAddress, static_cast<int>(slider.getValue()));
        else       audioProcessor.sendOSC(oscAddress, static_cast<float>(slider.getValue()));
    };
    slider.onContextMenuRequested = [this, controlId] { showSliderContextMenu(controlId); };
    addAndMakeVisible(slider);

    configureNameLabel(nameLabel, name, accent);
    configureValueLabel(valueLabel);
    refreshValueLabel(controlId);
}

void AriaBridgeAudioProcessorEditor::configureActionButton(MidiLearnButton& button, const juce::String& text, juce::Colour accent)
{
    button.setButtonText(text);
    button.setColour(juce::TextButton::buttonColourId, accent);
    button.setColour(juce::TextButton::buttonOnColourId, accent);
    addAndMakeVisible(button);
}

void AriaBridgeAudioProcessorEditor::configureNameLabel(juce::Label& label, const juce::String& text, juce::Colour accent)
{
    label.setText(text, juce::dontSendNotification);
    label.setJustificationType(juce::Justification::centred);
    label.setColour(juce::Label::textColourId, accent.withAlpha(0.92f));
    label.setFont(juce::Font(11.0f, juce::Font::bold));
    addAndMakeVisible(label);
}

void AriaBridgeAudioProcessorEditor::configureValueLabel(juce::Label& label)
{
    label.setJustificationType(juce::Justification::centred);
    label.setColour(juce::Label::textColourId, ink);
    label.setFont(juce::Font(12.5f, juce::Font::bold));
    addAndMakeVisible(label);
}

void AriaBridgeAudioProcessorEditor::refreshValueLabel(AriaBridgeAudioProcessor::ControlId controlId)
{
    auto& slider = getSliderForControl(controlId);
    auto& valueLabel = getValueLabelForControl(controlId);
    const auto isFloatControl = controlId == AriaBridgeAudioProcessor::ControlId::temp
        || controlId == AriaBridgeAudioProcessor::ControlId::topP
        || controlId == AriaBridgeAudioProcessor::ControlId::minP;

    valueLabel.setText(isFloatControl ? formatFloatValue(slider.getValue()) : formatIntValue(slider.getValue()),
                       juce::dontSendNotification);
}

void AriaBridgeAudioProcessorEditor::showSliderContextMenu(AriaBridgeAudioProcessor::ControlId controlId)
{
    juce::PopupMenu menu;
    menu.addItem(1, "MIDI Learn");
    const auto mappedCc = audioProcessor.getMappedMidiCC(controlId);
    if (mappedCc >= 0)
        menu.addItem(2, "Clear MIDI mapping (CC " + juce::String(mappedCc) + ")");

    menu.showMenuAsync(juce::PopupMenu::Options(),
                       [this, controlId] (int result)
                       {
                           if (result == 1) audioProcessor.beginMidiLearn(controlId);
                           else if (result == 2) audioProcessor.clearMidiMapping(controlId);
                       });
}

LearnableSlider& AriaBridgeAudioProcessorEditor::getSliderForControl(AriaBridgeAudioProcessor::ControlId controlId)
{
    switch (controlId)
    {
        case AriaBridgeAudioProcessor::ControlId::temp: return tempSlider;
        case AriaBridgeAudioProcessor::ControlId::topP: return topPSlider;
        case AriaBridgeAudioProcessor::ControlId::minP: return minPSlider;
        case AriaBridgeAudioProcessor::ControlId::tokens: return tokensSlider;
        case AriaBridgeAudioProcessor::ControlId::grade: return gradeSlider;
        default: break;
    }
    jassertfalse;
    return tempSlider;
}

juce::Label& AriaBridgeAudioProcessorEditor::getValueLabelForControl(AriaBridgeAudioProcessor::ControlId controlId)
{
    switch (controlId)
    {
        case AriaBridgeAudioProcessor::ControlId::temp: return tempValueLabel;
        case AriaBridgeAudioProcessor::ControlId::topP: return topPValueLabel;
        case AriaBridgeAudioProcessor::ControlId::minP: return minPValueLabel;
        case AriaBridgeAudioProcessor::ControlId::tokens: return tokensValueLabel;
        case AriaBridgeAudioProcessor::ControlId::grade: return gradeValueLabel;
        default: break;
    }
    jassertfalse;
    return tempValueLabel;
}

void AriaBridgeAudioProcessorEditor::applyMidiButtonTrigger(AriaBridgeAudioProcessor::ControlId buttonId)
{
    getButtonForControl(buttonId).triggerClick();
}

void AriaBridgeAudioProcessorEditor::showButtonContextMenu(AriaBridgeAudioProcessor::ControlId buttonId)
{
    juce::PopupMenu menu;
    menu.addItem(1, "MIDI Learn");
    const auto mappedMidi = audioProcessor.getMappedButtonMidi(buttonId);
    if (mappedMidi >= 0)
    {
        juce::String clearLabel = mappedMidi >= 128
            ? "Clear MIDI mapping (Note " + juce::String(mappedMidi - 128) + ")"
            : "Clear MIDI mapping (CC " + juce::String(mappedMidi) + ")";
        menu.addItem(2, clearLabel);
    }

    menu.showMenuAsync(juce::PopupMenu::Options(),
                       [this, buttonId] (int result)
                       {
                           if (result == 1) audioProcessor.beginMidiLearn(buttonId);
                           else if (result == 2) audioProcessor.clearMidiMapping(buttonId);
                       });
}

MidiLearnButton& AriaBridgeAudioProcessorEditor::getButtonForControl(AriaBridgeAudioProcessor::ControlId buttonId)
{
    switch (buttonId)
    {
        case AriaBridgeAudioProcessor::ControlId::record: return recordButton;
        case AriaBridgeAudioProcessor::ControlId::play:   return playButton;
        case AriaBridgeAudioProcessor::ControlId::cancel: return cancelButton;
        case AriaBridgeAudioProcessor::ControlId::sync:   return syncButton;
        case AriaBridgeAudioProcessor::ControlId::commit: return commitButton;
        default: break;
    }
    jassertfalse;
    return recordButton;
}

void AriaBridgeAudioProcessorEditor::configureStandaloneWindowIfNeeded()
{
    if (standaloneWindowConfigured)
        return;

    if (auto* window = dynamic_cast<juce::ResizableWindow*>(getTopLevelComponent()))
    {
        window->setResizable(true, true);
        window->setResizeLimits(560, 300, 1500, 760);
        window->setConstrainer(&windowConstrainer);
        standaloneWindowConfigured = true;
    }
}

void AriaBridgeAudioProcessorEditor::setGenerationActive(bool active)
{
    isGenerating = active;
    if (active)
    {
        genProgress = 0.0;
        playbackDisplayed = 0.0;   // bar starts empty and rises with generation
        progressLabel.setText("GEN 0%", juce::dontSendNotification);
    }
    else if (! isPlaying)
    {
        progressLabel.setText({}, juce::dontSendNotification);
    }
}

void AriaBridgeAudioProcessorEditor::setGenerationProgress(float value)
{
    isGenerating = true;
    genProgress = juce::jlimit(0.0, 1.0, static_cast<double>(value));
    progressLabel.setText("GEN " + juce::String(juce::roundToInt(genProgress * 100.0)) + "%", juce::dontSendNotification);
}

void AriaBridgeAudioProcessorEditor::timerCallback()
{
    phase += 1.0;

    // One eased bar value, target driven by whichever phase is active.
    const double target = isGenerating ? genProgress : (isPlaying ? playbackTarget : 0.0);
    playbackDisplayed += (target - playbackDisplayed) * 0.25;

    // Re-evaluate connection so it clears when the backend goes quiet.
    if (audioProcessor.getOSCStateSnapshot().connected != isConnected)
        refreshStatusDisplay();

    repaint();
}

void AriaBridgeAudioProcessorEditor::setPlaybackDuration(float seconds)
{
    playbackTotalDuration = static_cast<double>(seconds);
}

void AriaBridgeAudioProcessorEditor::setPlaybackProgress(float value)
{
    isPlaying = true;
    isGenerating = false;
    playbackTarget = juce::jlimit(0.0, 1.0, static_cast<double>(value));
    const double remaining = playbackTotalDuration * (1.0 - playbackTarget);
    progressLabel.setText(formatClock(juce::roundToInt(remaining)) + " left", juce::dontSendNotification);
}

void AriaBridgeAudioProcessorEditor::stopPlayback()
{
    isPlaying = false;
    playbackTarget = 0.0;
    if (! isGenerating)
        progressLabel.setText({}, juce::dontSendNotification);
}
