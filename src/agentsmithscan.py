#!/usr/bin/env python
"""
neoscan

Identifies neoepitopes from DNA-seq, VCF, GTF, and Bowtie index.
"""
import bisect
import argparse
import bowtie_index
import sys
import math
import string
import copy
import pickle
import defaultdict
import copy
import os
import random
import re
from intervaltree import Interval, IntervalTree
import tempfile
#import Hapcut2interpreter as hap

# X below denotes a stop codon
_codon_table = {
        "TTT":"F", "TTC":"F", "TTA":"L", "TTG":"L",
        "TCT":"S", "TCC":"S", "TCA":"S", "TCG":"S",
        "TAT":"Y", "TAC":"Y", "TAA":"X", "TAG":"X",
        "TGT":"C", "TGC":"C", "TGA":"X", "TGG":"W",
        "CTT":"L", "CTC":"L", "CTA":"L", "CTG":"L",
        "CCT":"P", "CCC":"P", "CCA":"P", "CCG":"P",
        "CAT":"H", "CAC":"H", "CAA":"Q", "CAG":"Q",
        "CGT":"R", "CGC":"R", "CGA":"R", "CGG":"R",
        "ATT":"I", "ATC":"I", "ATA":"I", "ATG":"M",
        "ACT":"T", "ACC":"T", "ACA":"T", "ACG":"T",
        "AAT":"N", "AAC":"N", "AAA":"K", "AAG":"K",
        "AGT":"S", "AGC":"S", "AGA":"R", "AGG":"R",
        "GTT":"V", "GTC":"V", "GTA":"V", "GTG":"V",
        "GCT":"A", "GCC":"A", "GCA":"A", "GCG":"A",
        "GAT":"D", "GAC":"D", "GAA":"E", "GAG":"E",
        "GGT":"G", "GGC":"G", "GGA":"G", "GGG":"G"
    }
_complement_table = string.maketrans("ATCG", "TAGC")

_help_intro = """neoscan searches for neoepitopes in seq data."""
def help_formatter(prog):
    """ So formatter_class's max_help_position can be changed. """
    return argparse.HelpFormatter(prog, max_help_position=40)

def seq_to_peptide(seq, reverse_strand=False):
    """ Translates nucleotide sequence into peptide sequence.

        All codons including and after stop codon are recorded as X's.

        seq: nucleotide sequence
        reverse_strand: True iff strand is -

        Return value: peptide string
    """
    seq_size = len(seq)
    if reverse_strand:
        seq = seq[::-1].translate(_complement_table)
    peptide = []
    for i in xrange(0, seq_size - seq_size % 3, 3):
        codon = _codon_table[seq[i:i+3]]
        peptide.append(codon)
        if codon == 'X':
            break
    for j in xrange(i + 3, seq_size - seq_size % 3, 3):
        peptide.append('X')
    return ''.join(peptide)

def kmerize_peptide(peptide, min_size=8, max_size=11):
    """ Obtains subsequences of a peptide.

        normal_peptide: normal peptide seq
        min_size: minimum subsequence size
        max_size: maximum subsequence size

        Return value: list of all possible subsequences of size between
            min_size and max_size
    """
    peptide_size = len(peptide)
    return [item for sublist in
                [[peptide[i:i+size] for i in xrange(peptide_size - size + 1)]
                    for size in xrange(min_size, max_size + 1)]
            for item in sublist if 'X' not in item]

def neoepitopes(mutation_positions, normal_seq, mutated_seq,
                        reverse_strand=False, min_size=8, max_size=11,
                        output_stream=sys.stdout):
    """ Finds neoepitopes from normal and mutated seqs.

        mutation_positions: list of mutation positions
        normal_seq: normal nucleotide sequence
        mutated_seq: mutated nucelotide sequence
        reverse_strand: True iff strand is -
        min_size: minimum peptide kmer size to write
        max_size: maximum petide kmer size to write

        Return value: List of tuples (normal_kmer, mutated_kmer)
    """
    return zip(kmerize_peptide(
        seq_to_peptide(
            normal_seq, reverse_strand=reverse_strand),
        min_size=min_size,
        max_size=max_size
    ), kmerize_peptide(
        seq_to_peptide(
            mutated_seq, reverse_strand=reverse_strand),
        min_size=min_size,
        max_size=max_size))


class Transcript(object):
    """ Transforms transcript with edits (SNPs, indels) from haplotype """

    def __init__(self, bowtie_reference_index, CDS):
        """ Initializes Transcript object

            bowtie_reference_index: BowtieIndexReference object for retrieving
                reference genome sequence
            CDS: list of all CDS lines for exactly one transcript from GTF
        """
        self.bowtie_reference_index = bowtie_reference_index
        self.intervals = []
        for line in CDS:
            tokens = line.strip().split('\t')
            self.intervals.extend(
                [(int(tokens[3]), True), (int(tokens[4]), True)]
            )
        self.edits = defaultdict(list)
        self.reference_intervals = copy.copy(self.intervals)
        '''Assume intervals are nonoverlapping! Uncomment following lines to
        check (slower).'''
        # for i in xrange(1, len(self.intervals)):
        #    if self.intervals[i-1] <= self.intervals[i]:
        #        raise RuntimeError(
        #                ('CDS intervals list '
        #                 '"{}" has overlapping intervals.').format(
        #                            self.intervals
        #                        )
        #            )
        # For retrieving save point
        self.last_edits = None
        self.last_intervals = None

    def reset(self, reference=False):
        """ Resets to last save point or reference (i.e., removes all edits).

            reference: if False, tries to reset to last save point, and if that
                doesn't exist, resets to reference. If True, resets to 
                reference.

            No return value.
        """
        if reference or self.last_edits is None:
            self.edits = []
            self.last_intervals = self.reference_intervals
        else:
            self.edits = self.last_edits
            self.intervals = self.last_intervals

    def edit(self, seq, pos, mutation_type='V'):
        """ Adds an edit to the transcript. 

            seq: sequence to add or delete from reference; for deletions, all
                that matters is this sequence has the same length as the 
                sequence to delete
            pos: 0-based coordinate. For insertions, this is the coordinate 
                directly preceding the inserted sequence. For deletions, this 
                is the coordinate of the first base of the transcript to be
                deleted. Coordinates are always w.r.t. genome.
            mutation_type: V for SNV, I for insertion, D for deletion

            No return value.
        """
        self.intervals.append((pos, False))
        self.edits[pos].append((seq, mutation_type))

    def edit_freq(self, pos, val):
        """modifies allele freq value at location """
        pass

    def get_freq(start=0, end=None, genome=True):
        """ Retrieves allele frequency list between start and end coordinates """
        pass

    def save():
        """ Creates save point for edits.

            No return value.
        """
        self.last_edits = copy.copy(self.edits)
        self.last_intervals = copy.copy(self.intervals)

    def seq(start=0, end=None, genome=False):
        """ Retrieves transcript sequence between start and end coordinates.

            start: start position (0-indexed); can be negative to measure from
                end of transcript, so -1 means the last base of the transcript,
                etc. Negative coordinates are always transcript coordinates.
            end: end position (0-indexed); None means end of transcript
            genome: True iff genome coordinates are specified

            Return value: transcript (sub)sequence
        """
        raise NotImplementedError
        assert end is None or end >= start
        self.intervals.sort()
        if genome:
            started = False
            start_index, end_index = 0, len(intervals) - 1
            for i, point in enumerate(self.intervals):
                if point[0] < start:
                    continue
                elif started:
                    if point[0] > end:
                        end_index = i
                else:
                    started = True
                    start_index = i
            # Accumulate transcript sequence
            seq = []
            reference_index.get_stretch(start, self.intervals[i])

        raise NotImplementedError(
            'Retrieving sequence with transcript coordinates not '
            'yet supported.'
        )



def get_seq(chrom, start, splice_length, reference_index):
    chr_name = "chr" + chrom #proper
    start -= 1 #adjust for 0-based bowtie queries
    try:
        seq = reference_index.get_stretch(chr_name, start, splice_length)
    except KeyError:
        return False
    return seq


def get_affinity_netmhcpan(peptides, allele, netmhcpan, remove_files=True):
    ''' Takes in a list of peptides and returns their binding affinities to an 
            allele as predicted by netMHCpan

        peptides: peptides of interest (list of strings)
        allele: Allele to use for binding affinity (string, format HLA-A02:01)
        remove_files: option to remove intermediate files

        Return value: affinities (a list of binding affinities as strings)
    '''

    # Check that allele is valid for method
    avail_alleles = pickle.load(open(os.path.dirname(__file__) + 
        "/availableAlleles.pickle", "rb"))
    allele = allele.replace("*", "")
    if allele not in avail_alleles["netMHCpan"]:
        sys.exit(allele + " is not a valid allele for netMHC")

    # Establish return list and sample id
    id = peptides[0] + "." + str(len(peptides)) + "." + allele + "." + method
    affinities = []

    # Write one peptide per line to a temporary file for input
    peptide_file = tempfile.mkstemp(suffix=".peptides", prefix="id.", text=True)
    with open(peptide_file[1], "w") as f:
        for sequence in peptides:
            f.write(sequence + "\n")

    # Establish temporary file to hold output
    mhc_out = tempfile.mkstemp(suffix=".netMHCpan.out", prefix="id.", text=True)

    # Run netMHCpan #### How do we establish the path? ####
    subprocess.call(
        [netmhcpan, "-a", allele, "-inptype", "1", "-p", "-xls", 
            "-xlsfile", mhc_out, peptide_file])
    with open(mhc_out[1], "r") as f:
        for line in f:
            if line[0] == "0":
                line = line.strip("\n").split("\t")
                nM = line[5]
                affinities.append(nM)

    # Remove temporary files
    if remove_files == True:
        subprocess.call(["rm", peptide_file[1]])
        subprocess.call(["rm", mhc_out[1]])

    return affinities

    
def get_affinity_netmhciipan(peptides, allele, netmhciipan, remove_files=True):
    ''' Takes in a list of peptides and returns their binding affinities to an 
            allele as predicted by netMHCIIpan

        peptides: peptides of interest (list of strings)
        allele: Allele to use for binding affinity (string)
        remove_files: option to remove intermediate files

        Return value: affinities (a list of binding affinities as strings)
    '''

    # Check that allele is valid for method
    avail_alleles = pickle.load(open(os.path.dirname(__file__)+
        "/availableAlleles.pickle", "rb"))
    allele = allele.replace("HLA-", "")
    if allele not in avail_alleles["netMHCIIpan"]:
        sys.exit(allele + " is not a valid allele for netMHCIIpan")

    # Establish return list and sample id
    id = peptides[0] + "." + str(len(peptides)) + "." + allele + "." + method
    affinities = []

    # Write one peptide per line to a temporary file for input if peptide 
    #   length is at least 9
	# Count instances of smaller peptides
    na_count = 0
    peptide_file = tempfile.mkstemp(suffix=".peptides", prefix="id.", text=True)
    with open(peptide_file[1], "w") as f:
        for sequence in peptides:
            if len(sequence) >= 9:
                f.write(sequence + "\n")
            else:
                na_count += 1
    if na_count > 0:
        print "Warning: " + str(na_count) + 
            " peptides not compatible with netMHCIIpan - will not receive score"

    # Establish temporary file to hold output
    mhc_out = tempfile.mkstemp(suffix=".netMHCIIpan.out", prefix="id.", 
        text=True)
    # Run netMHCIIpan (### How to establish path? ####)
    subprocess.call([netmhciipan, "-a", allele, "-inptype", "1", 
        "-xls", "-xlsfile", mhc_out, peptide_file])
    # Retrieve scores for valid peptides
    score_dict = {}
    with open(mhc_out[1], "r") as f:
        for line in f:
            line = line.split("\t")
            if line[0] != "" and line[0] != "Pos":
                pep = line[1]
                score = line[4]
                score_dict[pep] = score

    # Produce list of scores for valid peptides
    # Invalid peptides receive "NA" score
    for sequence in peptides:
        if sequence in score_dict:
            nM = score_dict[sequence]
        else:
            nM = "NA"
            affinities.append(nM)  # Remove temporary files

    if remove_files == True:
        subprocess.call(["rm", peptide_file[1]])
        subprocess.call(["rm", mhc_out[1]])

    return affinities


def go():
    """ Entry point """
    # Print file's docstring if -h is invoked
    parser = argparse.ArgumentParser(description=_help_intro, 
                formatter_class=help_formatter)
    try:
        if args.vcf == '-':
            if sys.stdin.isatty():
                raise RuntimeError('Nothing piped into this script, but input is '
                                   'to be read from stdin')
            else:
                input_stream = sys.stdin
        else:
            input_stream = open(args.vcf, "r")
            line_count = 0
            trans_lines = []
            my_dict = cds_dict
            #print(get_seq("3", 69990, 20, reference_index))
            for line in input_stream:
                line_count += 1
                if not line or line[0] == '#': continue
                vals = line.strip().split('\t')
                info = vals[7]
                tokens = info.strip().split('|')
                (trans_id) = (tokens[6])
                if((len(trans_lines) != 0) and (last_trans != trans_id)):
                    try:
                        direct = orf_dict[last_trans][0][0]
                        cds_list = cds_dict[last_trans]
                    except:
                        print "orf_dict failure"
                        last_trans = trans_id
                        trans_lines = []
                        continue
                    #print "here"
                    if(direct == "-"):
                        trans_lines = list(reversed(trans_lines))
                    begin_line = line_count - len(trans_lines) - 1
                    print "Before kmerize trans", last_trans, len(trans_lines)
                    kmerize_trans(trans_lines, begin_line, last_trans, cds_list, direct)
                    trans_lines = []
                last_trans = trans_id
                trans_lines.append(line)
            try:
                direct = orf_dict[last_trans][0][0]
            except KeyError:
                pass
            begin_line = line_count - len(trans_lines) - 1
            if(direct == "-"):
                trans_lines = list(reversed(trans_lines))
            try:
                cds_list = cds_dict[trans_id]
                kmerize_trans(trans_lines, begin_line, last_trans, cds_list, direct)
            except KeyError:
                pass

    finally:
        if args.vcf != '-':
            input_stream.close()
    try:
        if "," in args.kmer_size:
            (_size_min, _size_max) = args.kmer_size.split(",")
            _size_min = int(_size_min)
            _size_max = int(_size_max)
        else:
            _size_min = int(args.kmer_size)
            _size_max = _size_min
        if (_size_min < 1 or _size_max < 1):
            except ValueError:
                print "Kmer size(s) must be >= 1"
                pass
        if (_size_max < _size_min):
            except ValueError:
                print "Max kmer size cannot be less than min kmer size"
                pass
    except:
        print "Unable to import kmer size from command line parameter, defaulting to 8-11aa kmers"
        _size_min = 8
        _size_max = 11


def which(path):
    """ Searches for whether executable is present and returns version

        path: path to executable

        Return value: None if executable not found, else string with software
            name and version number
    """
    try:
        subprocess.check_call([path])
    except OSError as e:
        return None
    else:
        return path

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-v', '--vcf', type=str, required=True,
            default='-',
            help='input VCF or "-" for stdin'
        )
    parser.add_argument('-x', '--bowtie-index', type=str, required=True,
            help='path to Bowtie index basename'
        )
    parser.add_argument('-d', '--dicts', type=str, required=False,
            help='input path to pickled CDS dictionary'
        )
    parser.add_argument('-g', '--gtf', type=str, required=False,
            help='input path to GTF file'
        )	
    parser.add_argument('-b', '--bam', action='store_true', required=False,
            default = False, help='T/F bam is used'
        )
    parser.add_argument('-k', '--kmer-size', type=str, required=False,
            default='8,11', help='kmer size for epitope calculation'
        )
    parser.add_argument('-m', '--method', type=str, required=False,
            default='-', 
            help='method for calculating epitope binding affinities'
        )
    parser.add_argument('-a', '--affinity-predictor', type=str, required=False,
            default='netMHCpan', 
            help='path to executable for binding affinity prediction software'
        )
    args = parser.parse_args()
    
    # Check affinity predictor
    program = which(args.affinity-predictor)
    if program == None:
        raise ValueError(program + " is not a valid software")
    elif "netMHCIIpan" in program:
        method = "netMHCIIpan"
    elif "netMHCpan" in program:
        method = "netMHCpan"
    else:
        raise ValueError(program + " is not a valid software")
    
    reference_index = bowtie_index.BowtieIndexReference(args.bowtie_index)
    try:
	if args.dicts is not None:
		with open(args.dicts, 'rb') as dict_stream:
			cds_dict = pickle.load(dict_stream)
	elif args.gtf is not None:
		cds_dict = gtf_to_cds(args.gtf) # gtf_to_cds(args.gtf, args.gtf + "pickle")
	else:
		raise ValueError("No CDS data available (-d or -g argument must be specified)"
	cds_tree = cds_to_tree(cds_dict)
    except:
	print "Unable to import CDS data from GTF file " + args.gtf
	
