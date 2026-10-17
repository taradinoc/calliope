package calliope;
import java.io.*;
import java.nio.file.*;
import java.util.*;
import sgdk.rescomp.Compiler;
import sgdk.rescomp.Processor;
import sgdk.rescomp.Resource;
import sgdk.rescomp.resource.Bin;

public final class CalliopeProcessor implements Processor {
    public String getId() { return "CALLIOPE"; }
    public Resource execute(String[] fields) throws Exception {
        if (fields.length != 3 || !fields[1].matches("[A-Za-z_][A-Za-z_0-9]*"))
            throw new IllegalArgumentException("CALLIOPE symbol \"bank.json\"");
        String home = System.getenv("CALLIOPE_HOME");
        if (home == null) throw new IllegalArgumentException("Set CALLIOPE_HOME to the Calliope project");
        String python = System.getenv("CALLIOPE_PYTHON");
        if (python == null) python = "python";
        Path manifest = Paths.get(Compiler.resDir).resolve(fields[2]).toAbsolutePath().normalize();
        Path converter = Paths.get(home, "tools", "clpconvert.py");
        Path temporary = Files.createTempDirectory("calliope-rescomp-");
        try {
            Path bank = temporary.resolve("bank.bin");
            String region = System.getenv("CALLIOPE_REGION");
            if (region == null) region = "ntsc";
            List<String> command = new ArrayList<>(Arrays.asList(python, converter.toString(), "compile", manifest.toString(),
                "--output", bank.toString(), "--region", region, "--cache-dir", Paths.get(home,"out","conversion-cache").toString()));
            String ids = System.getenv("CALLIOPE_IDS_HEADER");
            if (ids != null) { command.add("--ids-header"); command.add(ids); }
            Process p = new ProcessBuilder(command).inheritIO().start();
            if (p.waitFor() != 0) throw new IOException("Calliope conversion failed: " + manifest);
            for (String input : Files.readAllLines(temporary.resolve("bank.deps")))
                if (!input.isEmpty()) Compiler.addResourceFile(input);
            Compiler.addResourceFile(Paths.get(Compiler.resDir,"rescomp_ext.jar").toAbsolutePath().toString());
            return new BankResource(fields[1], Files.readAllBytes(bank));
        } finally {
            try (java.util.stream.Stream<Path> files = Files.walk(temporary)) {
                for (Path f : (Iterable<Path>)files.sorted(Comparator.reverseOrder())::iterator) Files.deleteIfExists(f);
            }
        }
    }
    public static final class BankResource extends Resource {
        private final byte[] bytes;
        BankResource(String id, byte[] bytes) { super(id); this.bytes=bytes; }
        public List<Bin> getInternalBinResources() { return Collections.emptyList(); }
        public int internalHashCode() { return Arrays.hashCode(bytes); }
        public boolean internalEquals(Object other) {
            return other instanceof BankResource && id.equals(((BankResource)other).id)
                && Arrays.equals(bytes,((BankResource)other).bytes);
        }
        public int shallowSize() { return bytes.length; }
        public int totalSize() { return bytes.length; }
        public void out(ByteArrayOutputStream binary, StringBuilder assembly, StringBuilder header) {
            assembly.append("\n.balign 32768\n.global ").append(id).append("\n").append(id).append(":\n");
            for (int i=0; i<bytes.length; i+=16) {
                assembly.append(".byte ");
                for (int j=i; j<Math.min(i+16,bytes.length); j++) {
                    if (j>i) assembly.append(',');
                    assembly.append(bytes[j]&255);
                }
                assembly.append('\n');
            }
            header.append("extern const u8 ").append(id).append('[').append(bytes.length).append("];\n");
            binary.reset();
        }
    }
}
