#!/usr/bin/env python

# requires ffmpeg and pocketsphinx
# for macs, install pocketsphix with brew following these instructions: https://github.com/watsonbox/homebrew-cmu-sphinx
# $ brew tap watsonbox/cmu-sphinx
# $ brew install --HEAD watsonbox/cmu-sphinx/cmu-sphinxbase
# $ brew install --HEAD watsonbox/cmu-sphinx/cmu-sphinxtrain # optional
# $ brew install --HEAD watsonbox/cmu-sphinx/cmu-pocketsphinx

import sys
import os
import subprocess
import argparse
import re
import random
import math
from pydub import AudioSegment


def prepare_files(files):
    '''Splits files into chunks and converts them to a format that pocketsphinx can deal wtih (16khz mono 16bit wav)'''
    prepared = []
    for f in files:
        converted = split_and_convert_to_wav(f)
        prepared += converted
    return prepared


def split_and_convert_to_wav(f):
    length = clip_length(f)
    split_length = 60
    split_count = int(math.ceil(length/float(split_length)))

    converted = []
    for n in range(0, split_count):
        split_start = split_length * n
        new_name = f + '.temp.' + str(n) + '.wav'
        subprocess.call(['ffmpeg', '-i', f, '-ss', str(split_start), '-t', str(split_length), '-acodec', 'pcm_s16le', '-ac', '1', '-ar', '16000', new_name])
        converted.append(new_name)

    return converted


def clip_length(f):
    '''Uses ffmpeg to find the length of the clip'''
    output = subprocess.Popen("ffmpeg -i '"+ f +"' 2>&1 | grep 'Duration'", shell = True, stdout = subprocess.PIPE).stdout.read()

    length_regexp = 'Duration: (\d{2}):(\d{2}):(\d{2})\.\d+,'
    re_length = re.compile(length_regexp)

    matches = re_length.search(output)

    if matches:
        length = int(matches.group(1)) * 3600 + \
                       int(matches.group(2)) * 60 + \
                       int(matches.group(3))
        return length
    else:
        raise SystemExit("Can't determine video length.")


def transcribe(files=[], pre=10, post=50):
    '''Uses pocketsphinx to transcribe audio files'''

    total = len(files)

    for i, f in enumerate(files):
        print str(i+1) + '/' + str(total) + ' Transcribing ' + f
        filename = f.replace('.temp.wav', '') + '.transcription.txt'
        transcript = subprocess.check_output(['pocketsphinx_continuous', '-infile', f, '-time', 'yes', '-logfn', '/dev/null', '-vad_prespeech', str(pre), '-vad_postspeech', str(post)])

        with open(filename, 'w') as outfile:
            outfile.write(transcript)

        os.remove(f)


def convert_timestamps(files):
    '''Converts pocketsphinx transcriptions to usable timestamps'''

    sentences = []

    for f in files:

        if not f.endswith('.transcription.txt'):
            f = f + '.transcription.txt'

        with open(f, 'r') as infile:
            lines = infile.readlines()

        lines = [re.sub(r'\(.*?\)', '', l).strip().split(' ') for l in lines]
        lines = [l for l in lines if len(l) == 4]

        seg_start = -1
        seg_end = -1

        for index, line in enumerate(lines):
            word, start, end, conf = line
            if word == '<s>' or word == '<sil>' or word == '</s>':
                if seg_start == -1:
                    seg_start = index
                    seg_end = -1
                else:
                    seg_end = index

                if seg_start > -1 and seg_end > -1:
                    words = lines[seg_start+1:seg_end]
                    start = float(lines[seg_start][1])
                    end = float(lines[seg_end][1])
                    if words:
                        sentences.append({'start': start, 'end': end, 'words': words, 'file': f})
                    if word == '</s>':
                        seg_start = -1
                    else:
                        seg_start = seg_end
                    seg_end = -1

    return sentences


def text(files):
    '''Returns the whole transcribed text'''
    sentences = convert_timestamps(files)
    out = []
    for s in sentences:
        out.append(' '.join([w[0] for w in s['words']]))
    return '\n'.join(out)


def search(query, files, mode='sentence', regex=False):
    '''Searches for words or sentences containing a search phrase'''
    out = []
    sentences = convert_timestamps(files)

    for s in sentences:
        if mode == 'sentence':
            words = [w[0] for w in s['words']]
            found = False
            if regex:
                found = re.search(query, ' '.join(words))
            else:
                if query.lower() in words:
                    found = True
            if found:
                out.append(s)
        elif mode == 'word':
            for word in s['words']:
                found = False
                if regex:
                    found = re.search(query, word[0])
                else:
                    if query.lower() == word[0]:
                        found = True
                if found:
                    try:
                        start = float(word[1])
                        end = float(word[2])
                        confidence = float(word[3])
                        out.append({'start': start, 'end': end, 'file': s['file'], 'words': word[0], 'confidence': confidence})
                    except:
                        continue

    return out


def franken_sentence(sentence, files):
    out = []
    for word in sentence.split(' '):
        results = search(word, files, mode='word')
        if len(results) > 0:
            #sorted(results, key=lambda k: k['confidence'])
            #out = out + [results[0]]
            out = out + [random.choice(results)]

    return out


def compose(segments, out='out.mp3', padding=0, crossfade=0, layer=False):
    '''Stiches together a new audiotrack'''

    files = {}

    working_segments = []

    audio = AudioSegment.empty()

    if layer:
        total_time = max([s['end'] - s['start'] for s in segments]) * 1000
        audio = AudioSegment.silent(duration=total_time)

    for i, s in enumerate(segments):
        try:
            start = s['start'] * 1000
            end = s['end'] * 1000
            f = s['file'].replace('.transcription.txt', '')
            if f not in files:
                if f.endswith('.wav'):
                    files[f] = AudioSegment.from_wav(f)
                elif f.endswith('.mp3'):
                    files[f] = AudioSegment.from_mp3(f)

            segment = files[f][start:end]

            print start, end, f

            if layer:
                audio = audio.overlay(segment, times=1)
            else:
                if i > 0:
                    audio = audio.append(segment, crossfade=crossfade)
                else:
                    audio = audio + segment

            if padding > 0:
                audio = audio + AudioSegment.silent(duration=padding)

            s['duration'] = len(segment)
            working_segments.append(s)
        except:
            continue

    audio.export(out, format=os.path.splitext(out)[1].replace('.', ''))
    return working_segments


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Audiogrep: splice together audio based on search phrases')

    parser.add_argument('--input', '-i', dest='inputfile', required=True, nargs='*', help='Source files to search through')
    parser.add_argument('--search', '-s', dest='search', help='Search term - to use a regular expression, use the -re flag')
    parser.add_argument('--regex', '-re', dest='regex', help='Use a regular expression for search', action='store_true')
    parser.add_argument('--output-mode', '-m', dest='outputmode', default='sentence', choices=['sentence', 'word', 'franken'], help='Splice together phrases, or single words, or "frankenstein" sentences')
    parser.add_argument('--output', '-o', dest='outputfile', default='supercut.mp3', help='Name of output file')
    parser.add_argument('--transcribe', '-t', dest='transcribe', action='store_true', help='Transcribe audio files')
    parser.add_argument('--padding', '-p', dest='padding', type=int, help='Milliseconds of padding between the audio segments')
    parser.add_argument('--crossfade', '-c', dest='crossfade', type=int, default=0, help='Crossfade between clips')
    parser.add_argument('--demo', '-d', dest='demo', action='store_true', help='Just display the search results without actually making the file')
    parser.add_argument('--layer', '-l', dest='layer', action='store_true', help='Overlay the audio segments')

    args = parser.parse_args()

    if not args.search and not args.transcribe:
        parser.error('Please transcribe files [--transcribe] or search [--search SEARCH] already transcribed files')

    if args.transcribe:
        try:
            devnull = open(os.devnull)
            subprocess.Popen(['pocketsphinx_continuous', '--invalid-args'], stdout=devnull, stderr=devnull).communicate()
        except OSError as e:
            if e.errno == os.errno.ENOENT:
                print 'Error: Please install pocketsphinx to transcribe files.'
                sys.exit()
        files = prepare_files(args.inputfile)
        transcribe(files)
        print text(files)

    if args.search:
        if args.outputmode == 'franken':
            segments = franken_sentence(args.search, args.inputfile)
        else:
            segments = search(args.search, args.inputfile, mode=args.outputmode, regex=args.regex)

        if len(segments) == 0:
            print 'No results for "' + args.search + '"'
            sys.exit()

        print 'Generating supercut'
        if args.demo:
            for s in segments:
                if args.outputmode == 'sentence':
                    print ' '.join([w[0] for w in s['words']])
                else:
                    print s['words']
        else:
            compose(segments, out=args.outputfile, padding=args.padding, crossfade=args.crossfade, layer=args.layer)
